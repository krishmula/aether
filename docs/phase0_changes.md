# Phase 0 Changes — Foundation & Cleanup

This document records every meaningful change made during Phase 0. It is
the audit trail for code reviewers and the source of content for the Phase 0
section of the project README.

---

## 0.1 — Code Audit & Refactor

**Status: Complete**

All components were already independently launchable via CLI entry points
registered in `pyproject.toml` before this phase was formally reviewed.
Audit confirmed:

- `aether-broker` → `aether/cli/run_broker.py`
- `aether-bootstrap` → `aether/cli/run_bootstrap.py`
- `aether-publishers` → `aether/cli/run_publishers.py`
- `aether-subscribers` → `aether/cli/run_subscribers.py`
- `aether-admin` → `aether/cli/admin.py`
- `aether-distributed` → `aether/cli/run_distributed.py`

Each CLI accepts `--host`, `--port`, `--config`, `--log-level`, and
`--log-file` (or equivalent component-specific flags). This is the
prerequisite for Docker containerization in Phase 1.

No code changes were required; phase marked complete.

---

## 0.2 — Structured Logging

**Status: Complete (pre-existing work)**

All `print()` calls replaced with Python's `logging` module.
Implementation in `aether/utils/log.py`:

- `QueueHandler` + `QueueListener` for non-blocking log emission from hot
  paths (broker receive loop, gossip delivery).
- `ContextVar` (`_msg_id_var`) propagates the current gossip `msg_id`
  through the call stack. `bind_msg_id()` / `reset_msg_id()` are called
  in `GossipBroker._handle_gossip_message()` so every log line emitted
  during message handling automatically includes the correlation ID.
- `JSONFormatter` for machine-readable structured output (used when
  `--log-file` is specified or `$TERM` is unset).
- `ColoredFormatter` for human-readable colored output in interactive
  terminals.

No code changes were required in this session; phase marked complete.

---

## 0.3 — Health / Status Endpoint

**Status: Complete**

Added lightweight HTTP `/status` endpoints to both the broker and the
bootstrap server using only stdlib (`http.server.ThreadingHTTPServer`).
Zero new dependencies.

### New file: `aether/gossip/status.py`

Four classes:

| Class | Purpose |
|---|---|
| `_StatusHandler` | `BaseHTTPRequestHandler` for broker — serves `GET /status` |
| `StatusServer` | Wraps `ThreadingHTTPServer` + daemon thread for the broker |
| `_BootstrapStatusHandler` | `BaseHTTPRequestHandler` for bootstrap server |
| `BootstrapStatusServer` | Wraps `ThreadingHTTPServer` + daemon thread for bootstrap |

Both handlers:
- Return `200 application/json` on `GET /status`.
- Return `404 application/json` on any other path.
- Override `log_message(format, *args)` to redirect HTTP access logs to
  the `aether` logger at `DEBUG` level (the parameter is named `format`
  to match the base class signature; `# noqa: A002` suppresses the
  shadowing lint).

**Broker `/status` response shape:**

```json
{
  "broker": "127.0.0.1:5001",
  "host": "127.0.0.1",
  "port": 5001,
  "status_port": 15001,
  "peers": ["127.0.0.1:5002", "127.0.0.1:5003"],
  "peer_count": 2,
  "subscribers": {"count": 3},
  "messages_processed": 1847,
  "seen_message_ids": 412,
  "uptime_seconds": 342.1,
  "snapshot_state": "idle"
}
```

`snapshot_state` is `"recording"` while a Chandy-Lamport snapshot is in
progress and `"idle"` otherwise.

**Bootstrap `/status` response shape:**

```json
{
  "host": "127.0.0.1",
  "port": 4000,
  "status_port": 14000,
  "registered_brokers": ["127.0.0.1:5001", "127.0.0.1:5002"],
  "broker_count": 2,
  "uptime_seconds": 18.3
}
```

### Modified: `aether/gossip/broker.py`

New fields on `GossipBroker.__init__`:

```python
self._start_time: float = time.time()
self._messages_processed: int = 0
self._status_port: Optional[int] = http_port
self._status_server: Optional[StatusServer] = (
    StatusServer(self, http_port) if http_port is not None else None
)
```

`_messages_processed` is incremented inside `_handle_gossip_message()`
under `self._lock` (already held there for dedup). `_status_server` is
started in `start()` and stopped in `stop()`.

Import order fix: `from aether.gossip.status import StatusServer` moved
above the `aether.network` import block to satisfy ruff's `I001`
(isort) rule.

### Modified: `aether/gossip/bootstrap.py`

New fields on `BootstrapServer.__init__`:

```python
self._lock = threading.Lock()
self._start_time: float = time.time()
self._status_port: Optional[int] = http_port
self._status_server: Optional[BootstrapStatusServer] = (
    BootstrapStatusServer(self, http_port) if http_port is not None else None
)
```

`self._lock` is new and critical: `registered_brokers` (a `set`) was
previously mutated from `_serve_loop` with no synchronisation. The HTTP
status handler reads it from a second thread. All mutations of
`registered_brokers` inside `_serve_loop` are now wrapped under
`with self._lock`.

`_status_server` is started in `start()` and stopped in `stop()`.

### Modified: `aether/cli/run_broker.py`

Added `--status-port` argument:

```
--status-port INT   HTTP status port (default: broker_port + 10000)
```

Passed as `http_port` to `GossipBroker(...)`.

### Modified: `aether/cli/run_bootstrap.py`

Added `--status-port` argument:

```
--status-port INT   HTTP status port (default: bootstrap_port + 10000)
```

Passed as `http_port` to `BootstrapServer(...)`. Startup log line updated
to include the status port.

### Port convention

| Component | TCP port | HTTP status port |
|---|---|---|
| Bootstrap server | 4000 (default) | 14000 (default) |
| Broker | N | N + 10000 |

### New file: `tests/unit/test_status.py`

8 unit tests across two test classes. Each test spins up a real
`ThreadingHTTPServer` on an ephemeral OS-assigned port in-process, hits
it with `urllib.request`, and asserts on the response. No daemon threads
from `GossipBroker.start()` or `BootstrapServer.start()` are involved —
objects are constructed only, keeping tests fast and leak-free.

| # | Class | Test | What it checks |
|---|---|---|---|
| 1 | `TestStatusServer` | `test_status_returns_200_json` | HTTP 200, `Content-Type: application/json` |
| 2 | `TestStatusServer` | `test_status_fields_shape` | All required keys present with correct types |
| 3 | `TestStatusServer` | `test_status_reflects_live_state` | Mutating broker state immediately visible |
| 4 | `TestStatusServer` | `test_snapshot_state_field` | `snapshot_state` flips between `idle` / `recording` |
| 5 | `TestStatusServer` | `test_unknown_path_returns_404` | 404 on `/`, `/health`, `/metrics`, `/notfound` |
| 6 | `TestBootstrapStatusServer` | `test_bootstrap_status_returns_200_json` | HTTP 200, `Content-Type: application/json` |
| 7 | `TestBootstrapStatusServer` | `test_bootstrap_status_fields_shape` | All required keys present with correct types |
| 8 | `TestBootstrapStatusServer` | `test_bootstrap_status_reflects_registered_brokers` | Registered brokers immediately visible |

---

## 0.4 — Integration Test Script

**Status: Complete**

### New file: `tests/integration/test_e2e.py`

End-to-end integration test that programmatically starts a full system
in-process, exercises message delivery, verifies the `/status` endpoint,
initiates a Chandy-Lamport distributed snapshot, and validates the result.

**What the test does:**

1. Allocates 6 ephemeral TCP ports (3 brokers + 1 bootstrap + 1 publisher
   + 1 subscriber) to avoid port conflicts with any locally running
   services.
2. Starts a `BootstrapServer` with status HTTP enabled.
3. Starts 3 `GossipBroker` instances with `snapshot_interval=60` (so the
   periodic timer does not fire during the test) and status HTTP enabled.
4. Wires brokers into a full mesh via `add_peer()`.
5. Starts a `NetworkSubscriber`, connects it to broker 0, and registers
   for `PayloadRange(0, 127)` (payloads 0–127 inclusive).
6. Starts a `NetworkPublisher` and publishes 20 messages with payloads
   in `[0, 127]` — all should be delivered to the subscriber.
7. Waits up to 5 s for all 20 messages to arrive; asserts delivery count.
8. Hits broker 0's `/status` HTTP endpoint and asserts the shape and
   content of the JSON response (peer count, subscriber count, messages
   processed).
9. Initiates a Chandy-Lamport snapshot on broker 0 via
   `initiate_snapshot()`; waits up to 5 s for at least one replica to
   appear on a peer broker.
10. Validates the snapshot: correct `broker_address`, non-empty
    `peer_brokers`, non-empty `seen_message_ids`.
11. Stops all components and reports pass/fail with a structured summary.

**Run it:**

```bash
python tests/integration/test_e2e.py
```

Expected output on success:

```
============================================================
E2E INTEGRATION TEST — Phase 0.4
============================================================
[PASS] bootstrap /status: 200 OK
[PASS] 20/20 messages delivered to subscriber
[PASS] broker /status: peer_count=2 subscribers=1 messages_processed>=1
[PASS] snapshot replicated to peer broker
[PASS] snapshot content valid
------------------------------------------------------------
RESULT: ALL CHECKS PASSED (5/5)
============================================================
```
