# Aether Observability Plan: Grafana + Loki via OpenTelemetry

## Overview

This document describes the full plan to integrate production-grade log observability into Aether. The design is **backend-agnostic by default**: logs flow through an OpenTelemetry Collector, which today exports to Grafana Loki, and can be redirected to Datadog (or any other backend) by changing a single config file — zero Python code changes required.

---

## 1. Why This Stack

| Concern | Answer |
|---------|--------|
| **Free forever** | Loki + Grafana are open source and self-hosted |
| **Vendor-agnostic** | OTel Collector decouples the app from the storage backend |
| **Datadog-ready** | Swap the OTel exporter, nothing else changes |
| **Zero new log format** | Aether already emits structured JSON; OTel reads it as-is |
| **Production pattern** | OTel is the CNCF standard adopted by Google, Microsoft, AWS |
| **Extensible** | Same pipeline adds metrics and distributed traces later |

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        aether-net (Docker bridge)                    │
│                                                                       │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐            │
│  │ broker-1 │  │ broker-2 │  │publisher │  │subscriber│  ...        │
│  │          │  │          │  │          │  │          │             │
│  │ stdlib   │  │ stdlib   │  │ stdlib   │  │ stdlib   │             │
│  │ logging  │  │ logging  │  │ logging  │  │ logging  │             │
│  │    │     │  │    │     │  │    │     │  │    │     │             │
│  │ OTel SDK │  │ OTel SDK │  │ OTel SDK │  │ OTel SDK │             │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘            │
│       │             │             │              │                   │
│       └─────────────┴─────────────┴──────────────┘                  │
│                             │ OTLP/gRPC (port 4317)                  │
│                     ┌───────▼────────┐                               │
│                     │ OTel Collector │                               │
│                     │                │                               │
│                     │ recv: otlp     │                               │
│                     │ proc: batch    │                               │
│                     │ exp:  loki     │◄── swap to datadog here       │
│                     └───────┬────────┘                               │
│                             │ HTTP push                              │
│                     ┌───────▼────────┐                               │
│                     │      Loki      │                               │
│                     │  (log store)   │                               │
│                     └───────┬────────┘                               │
│                             │                                        │
│                     ┌───────▼────────┐                               │
│                     │    Grafana     │  ← http://localhost:3001      │
│                     │  (query + UI)  │                               │
│                     └────────────────┘                               │
│                                                                       │
│  ┌──────────────────┐  ┌──────────────────┐                         │
│  │   bootstrap      │  │  orchestrator    │  (existing services)    │
│  └──────────────────┘  └──────────────────┘                         │
└─────────────────────────────────────────────────────────────────────┘
```

### Log record journey

```
Python log call
  → BoundLogger.process()      # injects broker=, msg_id=, etc.
  → QueueHandler               # non-blocking enqueue (existing)
  → QueueListener              # drains queue on dedicated thread (existing)
      ├── StreamHandler        # colored stdout (existing, unchanged)
      ├── RotatingFileHandler  # optional JSON file (existing, unchanged)
      └── OTELHandler (new)    # converts to OTel LogRecord
            → BatchLogRecordProcessor
            → OTLPLogExporter
            → OTel Collector (gRPC 4317)
            → Loki
            → Grafana
```

---

## 3. New Files & Directory Structure

```
aether/
├── observability/                          ← new top-level directory
│   ├── otel-collector.yaml                 ← OTel Collector pipeline config
│   ├── loki.yaml                           ← Loki storage config
│   └── grafana/
│       └── provisioning/
│           ├── datasources/
│           │   └── loki.yaml               ← auto-wires Loki datasource
│           └── dashboards/
│               ├── provider.yaml           ← tells Grafana where to find dashboards
│               └── aether-logs.json        ← pre-built log explorer dashboard
│
├── aether/
│   ├── utils/
│   │   └── log.py                          ← add setup_otel_logging(), OTELHandler
│   └── config.py                           ← add log_otel_endpoint field
│
├── config.docker.yaml                      ← add otel_endpoint: http://otel-collector:4317
├── docker-compose.yml                      ← add otel-collector, loki, grafana services
└── pyproject.toml                          ← add opentelemetry dependencies
```

---

## 4. Phase 1: Infrastructure

### 4.1 New Python dependencies

**`pyproject.toml`** — add to `[project] dependencies`:

```toml
"opentelemetry-sdk>=1.25.0",
"opentelemetry-exporter-otlp-proto-grpc>=1.25.0",
```

> Check [PyPI](https://pypi.org/project/opentelemetry-sdk/) for the latest stable version before implementing.

### 4.2 Docker Compose additions

**`docker-compose.yml`** — append three services and two volumes:

```yaml
  # ── otel-collector ──────────────────────────────────────────────────────────
  otel-collector:
    image: otel/opentelemetry-collector-contrib:latest
    container_name: aether-otel-collector
    hostname: otel-collector
    command: ["--config=/etc/otelcol-contrib/config.yaml"]
    volumes:
      - ./observability/otel-collector.yaml:/etc/otelcol-contrib/config.yaml:ro
    ports:
      - "4317:4317"    # OTLP gRPC receiver
      - "4318:4318"    # OTLP HTTP receiver
    networks:
      - aether-net
    depends_on:
      - loki
    restart: unless-stopped

  # ── loki ────────────────────────────────────────────────────────────────────
  loki:
    image: grafana/loki:3.0.0
    container_name: aether-loki
    hostname: loki
    command: ["-config.file=/etc/loki/local-config.yaml"]
    volumes:
      - ./observability/loki.yaml:/etc/loki/local-config.yaml:ro
      - loki-data:/loki
    ports:
      - "3100:3100"    # Loki HTTP API (direct query access)
    networks:
      - aether-net
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://localhost:3100/ready"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 10s
    restart: unless-stopped

  # ── grafana ─────────────────────────────────────────────────────────────────
  grafana:
    image: grafana/grafana:11.0.0
    container_name: aether-grafana
    hostname: grafana
    environment:
      GF_AUTH_ANONYMOUS_ENABLED: "true"
      GF_AUTH_ANONYMOUS_ORG_ROLE: "Admin"
      GF_AUTH_DISABLE_LOGIN_FORM: "true"
      GF_FEATURE_TOGGLES_ENABLE: "lokiLive"
    volumes:
      - ./observability/grafana/provisioning:/etc/grafana/provisioning:ro
      - grafana-data:/var/lib/grafana
    ports:
      - "3001:3000"    # Grafana UI — http://localhost:3001
    networks:
      - aether-net
    depends_on:
      loki:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://localhost:3000/api/health"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 15s
    restart: unless-stopped

volumes:
  loki-data:
  grafana-data:
```

> Note: `GF_AUTH_ANONYMOUS_ENABLED=true` gives passwordless access for local development. Remove this for any shared/production deployment.

### 4.3 OTel Collector config

**`observability/otel-collector.yaml`**:

```yaml
# OpenTelemetry Collector — Aether log pipeline
# Receives OTLP from Python containers, exports to Loki.
# To switch to Datadog: add the datadog exporter below and update the pipeline.

receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318

processors:
  batch:
    # Batch log records before exporting to reduce HTTP overhead.
    timeout: 1s
    send_batch_size: 512

  resource:
    # Promote container-level metadata to top-level attributes for Loki labels.
    attributes:
      - key: service.name
        action: upsert
        from_attribute: service.name

exporters:
  loki:
    endpoint: http://loki:3100/loki/api/v1/push
    # Map OTel resource + log attributes to Loki stream labels.
    # Labels are indexed and queryable; keep them low-cardinality.
    labels:
      resource:
        service.name: "service_name"
      attributes:
        level: ""          # maps log record severity to {level="info"} etc.
        broker: ""         # BoundLogger context field → {broker="172.18.0.4:5001"}
        publisher: ""
        subscriber: ""
        bootstrap: ""

  # ── Future: Datadog ─────────────────────────────────────────────────────────
  # Uncomment and set DD_API_KEY in docker-compose environment to switch backends.
  #
  # datadog:
  #   api:
  #     site: datadoghq.com
  #     key: ${DD_API_KEY}
  #   logs:
  #     enabled: true

service:
  pipelines:
    logs:
      receivers:  [otlp]
      processors: [resource, batch]
      exporters:  [loki]
      # exporters: [loki, datadog]   ← run both during a migration window
```

### 4.4 Loki config

**`observability/loki.yaml`**:

```yaml
# Grafana Loki — minimal single-process config for local deployment.
# Uses filesystem storage (fine for development; swap to S3/GCS for production).

auth_enabled: false

server:
  http_listen_port: 3100
  grpc_listen_port: 9096
  log_level: warn

common:
  instance_addr: 127.0.0.1
  path_prefix: /loki
  storage:
    filesystem:
      chunks_directory: /loki/chunks
      rules_directory:  /loki/rules
  replication_factor: 1
  ring:
    kvstore:
      store: inmemory

schema_config:
  configs:
    - from: 2024-01-01
      store:        tsdb
      object_store: filesystem
      schema:       v13
      index:
        prefix: loki_index_
        period: 24h

limits_config:
  reject_old_samples:         true
  reject_old_samples_max_age: 168h   # 7 days
  ingestion_rate_mb:          16
  ingestion_burst_size_mb:    32

compactor:
  working_directory: /loki/compactor
  compaction_interval: 10m
```

### 4.5 Grafana provisioning

**`observability/grafana/provisioning/datasources/loki.yaml`**:

```yaml
# Auto-provisions the Loki datasource on first Grafana start.
# No manual configuration required.

apiVersion: 1

datasources:
  - name: Loki
    type: loki
    uid: loki-aether
    url: http://loki:3100
    isDefault: true
    version: 1
    editable: false
    jsonData:
      maxLines: 5000
      derivedFields:
        # Make msg_id a clickable trace link when tracing is added later.
        - name: msg_id
          matcherRegex: '"msg_id":"([^"]+)"'
          url: ""
          datasourceUid: ""
```

**`observability/grafana/provisioning/dashboards/provider.yaml`**:

```yaml
apiVersion: 1

providers:
  - name: Aether
    orgId: 1
    type: file
    disableDeletion: false
    updateIntervalSeconds: 30
    allowUiUpdates: true
    options:
      path: /etc/grafana/provisioning/dashboards
```

**`observability/grafana/provisioning/dashboards/aether-logs.json`**:

The pre-built dashboard JSON is generated after first run (see Section 6, Verification). On first launch, the Loki datasource is pre-wired and the Explore page is immediately usable. Useful starter queries to save as a dashboard:

```logql
# All logs from all Aether containers
{service_name="aether"}

# Only broker logs
{service_name="aether", broker!=""}

# Errors and warnings across all components
{service_name="aether"} | json | level =~ "error|warning"

# Logs for a specific broker
{broker="172.18.0.4:5001"}

# Follow a message through the system by correlation ID
{service_name="aether"} | json | msg_id="<uuid>"

# Gossip traffic rate over time (metric query)
sum(rate({service_name="aether"} | json | logger=~".*gossip.*" [1m]))
```

---

## 5. Phase 2: Python OTel Integration

### 5.1 Config changes

**`aether/config.py`** — add one field to the `Config` dataclass:

```python
@dataclass
class Config:
    log_level: str = "INFO"
    log_file: Optional[str] = None
    log_json_console: bool = False
    log_otel_endpoint: Optional[str] = None   # ← new
```

**`config.docker.yaml`** — add OTel endpoint:

```yaml
logging:
  level: INFO
  log_file: null
  json_console: true
  otel_endpoint: "http://otel-collector:4317"   # ← new
```

`config.yaml` (local/Tailscale mode) — leave `otel_endpoint: null`. OTel is opt-in; local dev is unaffected.

### 5.2 `aether/utils/log.py` changes

Two additions to `setup_logging()`:

1. Accept an `otel_endpoint: Optional[str] = None` parameter.
2. When set, create an `OTELHandler` and append it to the `handlers` list before starting the `QueueListener`.

```python
def setup_logging(
    level: str = "INFO",
    log_file: Optional[str] = None,
    json_console: bool = False,
    otel_endpoint: Optional[str] = None,    # ← new parameter
) -> None:
```

New `_make_otel_handler()` helper (add near the top of the module, guarded by a try/import so missing packages don't break local runs):

```python
def _make_otel_handler(endpoint: str, service_name: str = "aether") -> logging.Handler:
    """Create a stdlib logging handler that exports records to an OTel Collector.

    Returns a plain NullHandler if the opentelemetry packages are not installed,
    so that local dev (no OTel) works without changes.
    """
    try:
        from opentelemetry._logs import set_logger_provider
        from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        from opentelemetry.sdk.resources import Resource

        resource = Resource.create({"service.name": service_name})
        exporter = OTLPLogExporter(endpoint=endpoint, insecure=True)
        provider = LoggerProvider(resource=resource)
        provider.add_log_record_processor(BatchLogRecordProcessor(exporter))
        set_logger_provider(provider)

        handler = LoggingHandler(level=logging.DEBUG, logger_provider=provider)
        return handler

    except ImportError:
        # opentelemetry packages not installed — silently fall back.
        return logging.NullHandler()
```

In `setup_logging()`, after the file handler block and before starting the listener:

```python
    # -- OTel handler (optional) -------------------------------------------
    if otel_endpoint:
        otel_handler = _make_otel_handler(otel_endpoint)
        otel_handler.setLevel(logging.DEBUG)
        handlers.append(otel_handler)
```

### 5.3 CLI entry points

Each CLI entry point calls `setup_logging()` with values from the config. The config loader already reads `log_otel_endpoint` from the YAML. Pass it through:

```python
# In each cli/*.py main() function — add the new kwarg:
setup_logging(
    level=cfg.log_level,
    log_file=cfg.log_file,
    json_console=cfg.log_json_console,
    otel_endpoint=cfg.log_otel_endpoint,   # ← new
)
```

Files affected:
- `aether/cli/admin.py`
- `aether/cli/distributed_admin.py`
- `aether/cli/run_bootstrap.py`
- `aether/cli/run_broker.py`
- `aether/cli/run_publishers.py`
- `aether/cli/run_subscribers.py`

### 5.4 What the BoundLogger context fields become in Loki

The `BoundLogger` injects fields like `broker`, `publisher`, `subscriber`, `msg_id` into every log record's `extra` dict. The `OTELHandler` picks these up as log record attributes and forwards them to the Collector. The Collector's Loki exporter config (Section 4.3) then promotes `broker`, `publisher`, `subscriber`, `bootstrap` to Loki **stream labels**, making them first-class filter dimensions in Grafana.

---

## 6. Phase 3: Grafana Dashboard Setup

### 6.1 First launch

```bash
docker compose up --build
```

Navigate to `http://localhost:3001`. Grafana opens with no login required (anonymous admin). The Loki datasource is pre-wired. Go to **Explore → Loki** and run:

```logql
{service_name="aether"}
```

### 6.2 Recommended dashboard panels

Build a dashboard with these panels and export the JSON to `observability/grafana/provisioning/dashboards/aether-logs.json`:

| Panel | Type | Query |
|-------|------|-------|
| **Log stream** | Logs | `{service_name="aether"} \| json` |
| **Error rate** | Time series | `sum(rate({service_name="aether"} \| json \| level="error" [1m]))` |
| **Log volume by component** | Bar gauge | `sum by (broker) (rate({broker!=""} [1m]))` |
| **Broker logs** | Logs | `{broker!=""} \| json` |
| **Publisher logs** | Logs | `{publisher!=""} \| json` |
| **Subscriber logs** | Logs | `{subscriber!=""} \| json` |
| **Message trace** | Logs | `{service_name="aether"} \| json \| msg_id=~".+"` |

### 6.3 Live tail

Grafana Loki supports live tail mode (the "Live" toggle in Explore). With `GF_FEATURE_TOGGLES_ENABLE=lokiLive` set (already in the compose config), you get a real-time log stream in the browser — equivalent to `tail -f` but with labels and filtering.

---

## 7. Makefile additions

Add to `Makefile`:

```makefile
grafana:         ## Open Grafana in browser
	open http://localhost:3001

logs-live:       ## Live tail all Aether logs in terminal (requires loki running)
	docker logs -f aether-otel-collector

loki-query:      ## Run a raw LogQL query against Loki (set Q= on the command line)
	curl -s "http://localhost:3100/loki/api/v1/query_range" \
		--data-urlencode 'query={service_name="aether"}' \
		--data-urlencode "start=$$(date -v-5M +%s)000000000" \
		--data-urlencode "end=$$(date +%s)000000000" \
		| python3 -m json.tool
```

---

## 8. Phase 4 (Future): Metrics via OTel

Once the log pipeline is in place, metrics follow naturally. The OTel Collector already supports a `prometheus` exporter, and Grafana has a built-in Prometheus datasource.

Add OTel metric instruments at key points in the Python code:

| Metric | Instrument | Location |
|--------|-----------|---------|
| `aether.messages.processed` | Counter | `gossip/broker.py` — each gossip dispatch |
| `aether.peers.active` | UpDownCounter | `gossip/broker.py` — peer join/evict |
| `aether.gossip.latency` | Histogram | `network/node.py` — round-trip time |
| `aether.subscribers.count` | UpDownCounter | `gossip/broker.py` — subscribe/unsubscribe |
| `aether.publisher.send_rate` | Counter | `network/publisher.py` — each publish |

Add to `otel-collector.yaml`:

```yaml
exporters:
  prometheus:
    endpoint: 0.0.0.0:8889   # scraped by Grafana

service:
  pipelines:
    metrics:
      receivers:  [otlp]
      processors: [batch]
      exporters:  [prometheus]
```

---

## 9. Phase 5 (Future): Distributed Tracing

Aether already has the key ingredient: `msg_id`, a UUID that threads through every gossip hop. Promoting this to a proper OTel trace requires:

1. **`aether/gossip/protocol.py`** — add `trace_context: Optional[str] = None` to `GossipMessage`.
2. **`aether/gossip/broker.py`** — when dispatching a received message:
   - Extract `trace_context` and restore it via `TraceContextTextMapPropagator.extract()`
   - Create a child span: `tracer.start_as_current_span("broker.forward")`
   - Inject new context into outgoing message before forwarding
3. Add `opentelemetry-exporter-otlp-proto-grpc` traces pipeline to the Collector.
4. Add Grafana Tempo service to `docker-compose.yml` for trace storage.
5. Link log `msg_id` to trace ID in the Grafana Loki datasource `derivedFields` config.

Result: click any log line in Grafana → jump to the full distributed trace showing the message's path through every broker hop with per-hop latencies.

---

## 10. Migrating to Datadog

When ready to switch backends, the only change is in `observability/otel-collector.yaml`. The Python code, docker-compose services, and application config are **untouched**.

**Step 1**: Add API key to `docker-compose.yml` orchestrator environment (or a `.env` file):

```yaml
environment:
  DD_API_KEY: your_key_here
```

**Step 2**: Update `observability/otel-collector.yaml` exporters + pipeline:

```yaml
exporters:
  datadog:
    api:
      site: datadoghq.com
      key: ${DD_API_KEY}
    logs:
      enabled: true

service:
  pipelines:
    logs:
      receivers:  [otlp]
      processors: [resource, batch]
      exporters:  [datadog]          # ← replace loki with datadog
      # exporters: [loki, datadog]   # ← or dual-write during migration window
```

**Step 3**: `docker compose up -d otel-collector`

Logs appear in Datadog Log Management under `service:aether`. The `broker`, `publisher`, `subscriber` labels become Datadog facets automatically.

To switch back: revert the exporter line. No data migration required; Loki's on-disk data is untouched.

---

## 11. Verification Checklist

```
[ ] docker compose up --build
[ ] http://localhost:3001 loads Grafana with no login
[ ] Loki datasource is pre-wired (Settings → Data Sources → Loki → Test)
[ ] Seed demo topology: POST http://localhost:9000/api/seed
[ ] Explore → Loki → run {service_name="aether"} → log lines appear
[ ] Verify broker label: {broker!=""} returns only broker logs
[ ] Verify level filter: {service_name="aether"} | json | level="warning"
[ ] Run Live tail toggle — confirm real-time streaming
[ ] Remove a broker via DELETE /api/brokers/1 — verify logs stop for that container
[ ] docker compose down → docker compose up → verify loki-data volume persists logs
[ ] (OTel swap test) Comment out loki exporter, uncomment datadog → confirm no Python changes needed
```

---

## 12. Summary of All Files Changed or Created

| File | Status | Change |
|------|--------|--------|
| `observability/otel-collector.yaml` | New | OTel pipeline config |
| `observability/loki.yaml` | New | Loki storage config |
| `observability/grafana/provisioning/datasources/loki.yaml` | New | Auto-wires Loki in Grafana |
| `observability/grafana/provisioning/dashboards/provider.yaml` | New | Dashboard file provider |
| `observability/grafana/provisioning/dashboards/aether-logs.json` | New | Pre-built dashboard (export after first run) |
| `docker-compose.yml` | Modified | Add otel-collector, loki, grafana services + volumes |
| `pyproject.toml` | Modified | Add opentelemetry-sdk, opentelemetry-exporter-otlp-proto-grpc |
| `aether/utils/log.py` | Modified | Add `_make_otel_handler()`, `otel_endpoint` param to `setup_logging()` |
| `aether/config.py` | Modified | Add `log_otel_endpoint: Optional[str] = None` to `Config` |
| `config.docker.yaml` | Modified | Add `otel_endpoint: http://otel-collector:4317` |
| `aether/cli/*.py` (6 files) | Modified | Pass `otel_endpoint` to `setup_logging()` |
| `Makefile` | Modified | Add `grafana`, `logs-live`, `loki-query` targets |
