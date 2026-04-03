# Aether Observability Implementation Plan

## Context

Build a full telemetry stack for Aether: structured logs → OTel Collector → Loki/Prometheus → Grafana.

**What's already done (Phase A1 — complete):**
- `aether/utils/log.py` has all structured fields: `event_type`, `recovery_id`, `snapshot_id`, `component_type`, `component_id`, etc. via `_CONTEXT_FIELDS`
- `BoundLogger`, `JSONFormatter`, `QueueHandler/QueueListener` async pipeline are complete
- `tests/unit/test_logging_schema.py` has 12 passing schema tests

---

## Architecture

```text
                            ┌────────────────────────────┐
                            │         Grafana :3001      │
                            │  dashboards + explore       │
                            └───────────┬────────────────┘
                                        │
                       ┌────────────────┴───────────────┐
                       │                                │
                       ▼                                ▼
              ┌──────────────────┐             ┌──────────────────┐
              │       Loki       │             │    Prometheus    │
              │  logs + events   │             │ metrics + rates  │
              └────────┬─────────┘             └────────┬─────────┘
                       │                                │
                       └──────────────┬─────────────────┘
                                      ▼
                           ┌────────────────────┐
                           │ OTel Collector     │
                           │ logs + metrics     │
                           └─────────┬──────────┘
                                     │
        ┌────────────────────────────┼────────────────────────────┐
        │                            │                            │
        ▼                            ▼                            ▼
┌────────────────┐          ┌────────────────┐          ┌────────────────┐
│ bootstrap      │          │ orchestrator   │          │ dynamic data   │
│ structured logs│          │ logs + metrics │          │ plane nodes    │
│ + status       │          │ + /metrics     │          │ brokers/pubs/  │
└────────────────┘          └────────────────┘          │ subs           │
                                                       └────────────────┘
```

---

## Implementation Steps

### Step 1 — Add packages to `pyproject.toml`

Add to `dependencies`:
```
"prometheus-client>=0.20.0",
"opentelemetry-api>=1.20.0",
"opentelemetry-sdk>=1.20.0",
"opentelemetry-exporter-otlp-proto-http>=1.20.0",
```

---

### Step 2 — Add `log_otel_endpoint` to config

**`aether/config.py`:**
- Add `log_otel_endpoint: Optional[str] = None` to `Config` dataclass
- In `from_yaml`: `log_otel_endpoint=data.get("logging", {}).get("otel_endpoint")`

**`config.docker.yaml`** (under logging section):
```yaml
logging:
  level: INFO
  log_file: null
  json_console: true
  otel_endpoint: null  # set to http://otel-collector:4318 to enable OTLP
```

---

### Step 3 — Add OTLP handler to `aether/utils/log.py`

**What this does and why it matters:**
Right now all structured logs stay local — stdout or a rotating file. Grafana and Loki can't
see them. This step adds one more sink to the existing async logging pipeline:

```
# Before
app code → QueueHandler → [QueueListener thread] → stdout / file

# After
app code → QueueHandler → [QueueListener thread] → stdout / file
                                                  → OTLPHandler → OTel Collector → Loki
```

`_OTLPHandler` is a standard Python `logging.Handler` that converts each log record into an
OpenTelemetry log record and ships it to the OTel Collector over HTTP. Because it attaches to
the `QueueListener` (not the calling thread), the application never blocks on a network call.

This is the bridge between Aether's application logs and the rest of the observability stack.
Without it the OTel Collector has nothing to receive, Loki has nothing to store, and Grafana's
log panels are empty. Every `event_type` field added in Steps 4–5 only becomes queryable in
Grafana because this handler ships it to Loki.

Add optional `otel_endpoint` parameter to `setup_logging`. Add `_OTLPHandler` class guarded by `try/ImportError` at module level (soft dependency — system runs without OTel packages installed).

```python
# Soft import guard at module top
try:
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    _OTEL_AVAILABLE = True
except ImportError:
    _OTEL_AVAILABLE = False

class _OTLPHandler(logging.Handler):
    def __init__(self, endpoint: str) -> None:
        super().__init__()
        exporter = OTLPLogExporter(endpoint=endpoint)
        provider = LoggerProvider()
        provider.add_log_record_processor(BatchLogRecordProcessor(exporter))
        self._otel_handler = LoggingHandler(level=logging.DEBUG, logger_provider=provider)
        self._provider = provider

    def emit(self, record: logging.LogRecord) -> None:
        self._otel_handler.emit(record)

    def close(self) -> None:
        self._provider.shutdown()
        super().close()
```

In `setup_logging`, add `otel_endpoint: Optional[str] = None` param. After file handler:
```python
if otel_endpoint and _OTEL_AVAILABLE:
    otlp = _OTLPHandler(f"{otel_endpoint}/v1/logs")
    otlp.setLevel(logging.DEBUG)
    handlers.append(otlp)
```

Update all 4 CLI entry points (`run_bootstrap.py`, `run_broker.py`, `run_publishers.py`,
`run_subscribers.py`) to add `otel_endpoint=config.log_otel_endpoint` to their `setup_logging(...)` calls.

---

### Step 4 — Unify orchestrator logging (Phase A2)

**`aether/orchestrator/main.py`** — replace `logging.basicConfig(...)` lines with:
```python
from aether.utils.log import setup_logging, BoundLogger

setup_logging(level="INFO", json_console=True)
logger = BoundLogger(
    logging.getLogger(__name__),
    {"service_name": "aether-orchestrator", "component_type": "orchestrator"},
)
```

Add `log_level: str = "INFO"` to `aether/orchestrator/settings.py`.

---

### Step 5 — Explicit event_type logs (Phase A3 + A4)

**Logger patterns in this codebase:**

There are two logger patterns and it matters which one a file uses:

1. **`self.log = BoundLogger(logger, {...})`** — used by classes that have a per-instance
   network identity (address, port). `GossipBroker`, `NetworkPublisher`, `NetworkSubscriber`,
   `NetworkNode`, and `BootstrapNode` all do this so every log call automatically carries the
   component's address without passing it explicitly.

2. **`logger = logging.getLogger(__name__)`** — used by everything else: CLI scripts, utility
   modules, and orchestrator service classes. No per-instance context to bind.

`RecoveryManager` and `HealthMonitor` were written before observability was planned. As
orchestrator-internal service classes — no network address, no instance identity — they
naturally got plain `getLogger`. When Step 4 unified orchestrator logging, only `main.py`
was explicitly converted to `BoundLogger`. Steps 5 and 7 bolted `extra={}` dicts onto
individual log calls in `recovery.py` and `health.py` but didn't change the module-level
logger type. This means those log records don't automatically carry `service_name` or
`component_type` — fields that the OTel Collector promotes to Loki stream labels.

**The fix:** both files should have a module-level `BoundLogger` wrapping their `getLogger`
with `{"service_name": "aether-orchestrator", "component_type": "orchestrator"}` — the same
fields `main.py` uses. The dashboard queries all filter on `event_type`, which IS in the
`extra={}` dicts, so nothing is broken — but without this fix you can't run a LogQL query
like `{component_type="orchestrator"}` and have recovery/health events show up.

**What this does and why it matters:**
The logging schema already supports `event_type`, `recovery_id`, and `snapshot_id` — but
nothing in the application sets them yet. Logs exist as free-text messages like
`"Broker 2 declared dead"`. You can't query, alert, or graph on a substring.

This step attaches stable, machine-readable field values to log calls that already exist at
key lifecycle moments. No new log calls — just `extra={"event_type": "..."}` on the ones
already there. It also threads a `recovery_id` UUID through the entire recovery flow so every
record across `health.py` → `recovery.py` → helpers all carry the same ID.

After this step you can write LogQL like:
```logql
{job="aether"} | json | recovery_id="abc-123"
```
...and get every log from the health check that noticed the failure through to the final
outcome, all correlated by one ID. Grafana's log panels can also filter by
`event_type="broker_declared_dead"` as an indexed label rather than a substring match.

This is also the prerequisite for Step 7 (Prometheus metrics) — recovery counters need to
know when a recovery succeeded or failed, which only works if the code tracks it explicitly.

Add `extra={"event_type": "..."}` to key lifecycle log calls in these files:

#### `aether/orchestrator/recovery.py`
- Add `import uuid`, `import time`
- Generate `recovery_id = str(uuid.uuid4())` and `_t0 = time.time()` at top of `handle_broker_dead`
- Pass both to `_recover_replacement` and `_recover_redistribution` as args

| Site | event_type | extra fields |
|---|---|---|
| After `BROKER_DECLARED_DEAD` broadcast | `broker_declared_dead` | broker_id, recovery_id |
| `_recover_replacement` start | `broker_recovery_started` | broker_id, recovery_id, recovery_path="replacement" |
| `_recover_replacement` success | `broker_recovered` | broker_id, recovery_id, recovery_path="replacement" |
| `_recover_redistribution` start | `broker_recovery_started` | broker_id, recovery_id, recovery_path="redistribution" |
| `_recover_redistribution` success | `broker_recovered` | broker_id, recovery_id, recovery_path="redistribution" |
| Failure paths | `broker_recovery_failed` | broker_id, recovery_id, error_kind |

#### `aether/orchestrator/health.py`
- On failure increment: `extra={"event_type": "broker_poll_failed", "broker_id": broker.component_id}`
- On threshold reached: `extra={"event_type": "broker_declared_dead", "broker_id": broker.component_id}`

#### `aether/gossip/broker.py`
- `add_peer` for new peer: `extra={"event_type": "peer_joined"}`
- `_check_heartbeat_loop` eviction: `extra={"event_type": "peer_evicted"}`
- Snapshot initiation: `extra={"event_type": "snapshot_started", "snapshot_id": snapshot_id}`
- Snapshot completion: `extra={"event_type": "snapshot_completed", "snapshot_id": snapshot.snapshot_id}`

#### `aether/network/subscriber.py`
- Reconnect success: `extra={"event_type": "subscriber_reconnected"}`

#### `aether/network/publisher.py`
- Send success: `extra={"event_type": "message_published"}`
- Send failure: `extra={"event_type": "send_failure", "error_kind": "send_error"}`

---

### Step 6 — New file: `aether/orchestrator/metrics.py`

Module-level Prometheus singletons imported by both `main.py` and `recovery.py`:

```python
from prometheus_client import Counter, Gauge, Histogram

broker_recoveries_total = Counter(
    "aether_broker_recoveries_total", "Total broker recovery attempts",
    ["recovery_path", "result"],
)
component_up = Gauge(
    "aether_component_up", "Whether a component is running (1=up, 0=down)",
    ["component_type", "component_id"],
)
recovery_duration_seconds = Histogram(
    "aether_recovery_duration_seconds", "Duration of a broker recovery attempt",
    buckets=[1, 2, 5, 10, 15, 30, 60],
)
messages_published_total = Counter(
    "aether_messages_published_total", "Messages published (from broker /status polling)",
)
broker_peer_count = Gauge(
    "aether_broker_peer_count", "Current peer count per broker", ["broker_id"],
)
broker_subscriber_count = Gauge(
    "aether_broker_subscriber_count", "Current subscriber count per broker", ["broker_id"],
)
subscriber_reconnects_total = Counter(
    "aether_subscriber_reconnects_total", "Subscriber reconnects tracked by orchestrator",
)
snapshot_runs_total = Counter(
    "aether_snapshot_runs_total", "Snapshot runs by result", ["result"],
)
```

---

### Step 7 — Wire metrics into orchestrator

**What this does and why it matters:**
Step 6 defined the metric objects — they exist but are all zero. Nothing updates them yet.
This step connects them to the live system via three pieces:

1. **`recovery.py`** — increments counters and records histogram observations at the exact
   success/failure branch points added in Step 5. This is the most important piece: recovery
   duration and outcome are Aether's primary SLOs.

2. **`main.py` `/metrics` endpoint** — a single new route that calls
   `prometheus_client.generate_latest()` and returns Prometheus text format. Without this,
   Prometheus has nowhere to pull data from and all graphs stay flat.

3. **`main.py` background poller** — gauges represent *current state*, not transitions, so
   they can't be event-driven. A lightweight `asyncio` task runs every 15s, calls the
   existing `docker_mgr.get_metrics()` (which already polls broker `/status`), and sets gauge
   values from the result. `messages_published_total` is also driven from here, incremented
   only by positive deltas to handle broker restarts resetting their internal counters.

After this step, killing a broker produces a visible spike in `aether_broker_recoveries_total`,
a data point in the recovery duration histogram, and `aether_component_up` drops to 0 for
that broker then returns to 1 after recovery completes.

**`aether/orchestrator/recovery.py`** — import from `.metrics` and update at recovery points:
```python
broker_recoveries_total.labels(recovery_path="replacement", result="success").inc()
recovery_duration_seconds.observe(time.time() - _t0)
subscriber_reconnects_total.inc(len(orphans))  # redistribution path
broker_recoveries_total.labels(recovery_path=path, result="failure").inc()  # failure path
```

**`aether/orchestrator/main.py`** — add `/metrics` endpoint:
```python
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

@app.get("/metrics")
async def prometheus_metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
```

Add background updater on startup (polls `docker_mgr.get_metrics()` every 15s) to keep
`component_up`, `broker_peer_count`, `broker_subscriber_count`, and `messages_published_total`
current. Track previous message total per broker to handle counter resets on container restart.

---

### Step 8 — Propagate OTel config to dynamic containers (Phase B2)

**What this does and why it matters:**
Even with the OTel Collector running, no process currently ships logs to it. This step
closes that gap via two targeted changes:

1. **Orchestrator ships its own logs.** `OrchestratorSettings` gets an `otel_endpoint`
   field (sourced from an env var so docker-compose can set it). `setup_logging` in
   `main.py` is updated to pass it through. The orchestrator's structured logs — including
   all `event_type` fields from Steps 4–5 — now flow to Loki.

2. **Dynamic containers inherit the endpoint via `config.docker.yaml`.** Brokers,
   publishers, and subscribers already mount `/app/config.docker.yaml` at startup and their
   CLI entry points already call `setup_logging(otel_endpoint=config.log_otel_endpoint)`
   (wired in Step 3). Setting `otel_endpoint: http://otel-collector:4318` in
   `config.docker.yaml` once is enough — every dynamic container that mounts it picks it up
   automatically. The hostname `otel-collector` resolves because all containers share
   `aether-net`. No per-container env var injection needed.

After this step, every process in the system ships structured logs to the same collector.

**`aether/orchestrator/settings.py`** — add:
```python
otel_endpoint: Optional[str] = None
```

Since all dynamic containers mount `/app/config.docker.yaml`, setting `otel_endpoint` there
is enough — the CLI scripts already read and pass `config.log_otel_endpoint` to `setup_logging`.

When running with the observability stack, set in `config.docker.yaml`:
```yaml
logging:
  otel_endpoint: http://otel-collector:4318
```

---

### Step 9 — Add observability services to `docker-compose.yml`

Add after `dashboard` service (Grafana on port 3001, not 3000 which is taken by dashboard):

```yaml
  otel-collector:
    image: otel/opentelemetry-collector-contrib:0.95.0
    container_name: aether-otel-collector
    hostname: otel-collector
    command: ["--config=/etc/otel-collector.yaml"]
    volumes:
      - ./observability/otel-collector.yaml:/etc/otel-collector.yaml:ro
    ports:
      - "4317:4317"
      - "4318:4318"
    networks:
      - aether-net
    restart: unless-stopped

  loki:
    image: grafana/loki:2.9.4
    container_name: aether-loki
    hostname: loki
    command: -config.file=/etc/loki/loki.yaml
    volumes:
      - ./observability/loki.yaml:/etc/loki/loki.yaml:ro
    ports:
      - "3100:3100"
    networks:
      - aether-net
    restart: unless-stopped

  prometheus:
    image: prom/prometheus:v2.50.1
    container_name: aether-prometheus
    hostname: prometheus
    volumes:
      - ./observability/prometheus.yaml:/etc/prometheus/prometheus.yml:ro
    ports:
      - "9090:9090"
    networks:
      - aether-net
    restart: unless-stopped

  grafana:
    image: grafana/grafana:10.3.3
    container_name: aether-grafana
    hostname: grafana
    environment:
      GF_SERVER_HTTP_PORT: "3001"
      GF_AUTH_ANONYMOUS_ENABLED: "true"
      GF_AUTH_ANONYMOUS_ORG_ROLE: "Viewer"
    volumes:
      - ./observability/grafana/provisioning:/etc/grafana/provisioning:ro
      - ./observability/grafana/dashboards:/var/lib/grafana/dashboards:ro
    ports:
      - "3001:3001"
    depends_on:
      - loki
      - prometheus
    networks:
      - aether-net
    restart: unless-stopped
```

---

### Step 10 — Provisioning files under `observability/`

**`observability/otel-collector.yaml`**
```yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318

processors:
  batch:
    timeout: 1s
    send_batch_size: 512
  resource:
    attributes:
      - action: insert
        key: service.namespace
        value: aether

exporters:
  loki:
    endpoint: http://loki:3100/loki/api/v1/push
    default_labels_enabled:
      exporter: false
      job: true
      level: true
    labels:
      resource:
        service.name: "service_name"
      record:
        event_type: "event_type"
        component_type: "component_type"

service:
  pipelines:
    logs:
      receivers: [otlp]
      processors: [resource, batch]
      exporters: [loki]
```

**`observability/loki.yaml`** — minimal single-node in-process config (no persistent volumes needed for local dev)

**`observability/prometheus.yaml`**
```yaml
global:
  scrape_interval: 15s
scrape_configs:
  - job_name: aether-orchestrator
    static_configs:
      - targets: ["orchestrator:9000"]
    metrics_path: /metrics
```

**`observability/grafana/provisioning/datasources/prometheus.yaml`**
```yaml
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
```

**`observability/grafana/provisioning/datasources/loki.yaml`**
```yaml
apiVersion: 1
datasources:
  - name: Loki
    type: loki
    access: proxy
    url: http://loki:3100
```

**`observability/grafana/provisioning/dashboards/provider.yaml`**
```yaml
apiVersion: 1
providers:
  - name: aether
    orgId: 1
    folder: Aether
    type: file
    disableDeletion: false
    updateIntervalSeconds: 30
    options:
      path: /var/lib/grafana/dashboards
```

---

### Step 11 — Grafana dashboards

**`observability/grafana/dashboards/aether-overview.json`**

Panels:
- Stat: running brokers/publishers/subscribers (`count(aether_component_up{component_type=X} == 1)`)
- Stat: total recoveries past 24h (`increase(aether_broker_recoveries_total[24h])`)
- Time series: message publish rate (`rate(aether_messages_published_total[1m])`)
- Time series: broker peer counts by broker_id
- Logs: `{job="aether"} | json`

**`observability/grafana/dashboards/aether-failover.json`**

Panels:
- Stat: recovery success/failure split by `result` label
- Time series: recovery duration p50/p95
- Time series: subscriber reconnect rate
- Logs: `{job="aether", event_type="broker_declared_dead"} | json`
- Logs: `{job="aether"} | json | event_type =~ "broker_recovery.*"`

---

## Critical Files

| File | Change |
|---|---|
| `pyproject.toml` | Add 4 packages |
| `aether/config.py` | Add `log_otel_endpoint: Optional[str]` |
| `aether/utils/log.py` | Add `_OTLPHandler`, `otel_endpoint` param |
| `aether/orchestrator/main.py` | Replace basicConfig, add `/metrics`, add startup updater |
| `aether/orchestrator/recovery.py` | Add recovery_id, event_type logs, metrics updates |
| `aether/orchestrator/health.py` | Add event_type logs at failure threshold |
| `aether/orchestrator/settings.py` | Add `otel_endpoint`, `log_level` |
| `aether/orchestrator/docker_manager.py` | (no change needed — config.docker.yaml is mounted) |
| `aether/gossip/broker.py` | Add event_type + snapshot_id to lifecycle logs |
| `aether/network/subscriber.py` | Add event_type to reconnect log |
| `aether/network/publisher.py` | Add event_type to send logs |
| `config.docker.yaml` | Add `otel_endpoint` under logging |
| `docker-compose.yml` | Add 4 observability services |
| `aether/orchestrator/metrics.py` | **New** — Prometheus metric singletons |
| `observability/otel-collector.yaml` | **New** |
| `observability/loki.yaml` | **New** |
| `observability/prometheus.yaml` | **New** |
| `observability/grafana/provisioning/datasources/prometheus.yaml` | **New** |
| `observability/grafana/provisioning/datasources/loki.yaml` | **New** |
| `observability/grafana/provisioning/dashboards/provider.yaml` | **New** |
| `observability/grafana/dashboards/aether-overview.json` | **New** |
| `observability/grafana/dashboards/aether-failover.json` | **New** |

---

## Key Gotchas

1. **OTel soft import guard** — wrap `_OTLPHandler` class in `try/except ImportError` so missing packages don't break imports in non-Docker dev
2. **Counter delta tracking** — `messages_published_total` tracks previous total per broker; only increment by positive deltas to handle container restarts resetting counters
3. **Loki stream cardinality** — do NOT index `recovery_id`, `snapshot_id`, or `msg_id` as stream labels; they go in log attributes queryable with `| json`
4. **Grafana port 3001** — dashboard already occupies port 3000
5. **Pin OTel Collector to `0.95.0`** — Loki exporter config schema changed between versions
6. **`recovery_id` scope** — generate once in `handle_broker_dead`, pass to both `_recover_replacement` and `_recover_redistribution`; one ID spans the whole attempt
7. **OTLP endpoint URL** — `OTLPLogExporter` in `>=1.20` appends `/v1/logs` automatically; use base URL `http://otel-collector:4318`
8. **`BoundLogger` vs plain `getLogger` in orchestrator sub-modules** — `recovery.py` and `health.py` use `logging.getLogger(__name__)` because they were written before observability was planned. They're orchestrator-internal service classes with no network address, so they got the plain-logger pattern. `extra={}` dicts on individual log calls supply `event_type` (which the dashboards query), but `service_name` and `component_type` are absent from those records. Fix: add a module-level `BoundLogger` with `{"service_name": "aether-orchestrator", "component_type": "orchestrator"}` to both files — matching the pattern already used in `main.py`.

---

## Acceptance Criteria

1. `docker compose up --build` starts all 7 services cleanly
2. `curl http://localhost:9000/metrics` returns Prometheus text with `aether_component_up`
3. Grafana at `http://localhost:3001` opens with dashboards pre-loaded and datasources connected
4. Killing a broker via chaos button produces:
   - `aether_broker_recoveries_total` increment in Prometheus
   - `broker_declared_dead` log in Loki logs panel
   - Recovery duration metric visible
5. `pytest tests/unit/test_logging_schema.py` — all 12 tests still pass
