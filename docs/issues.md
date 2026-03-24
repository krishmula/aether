# Orchestrator Issues

Discovered during audit of the orchestrator's state management, API endpoints, and container lifecycle. Ordered by severity.

---

## Issue #1: ComponentStatus never transitions from STARTING to RUNNING

**Severity:** Critical
**File:** `aether/orchestrator/docker_manager.py`

**Description:**
`ComponentInfo.status` defaults to `ComponentStatus.STARTING` but is never updated to `RUNNING` anywhere in the codebase. The only place `RUNNING` is checked is `_running_broker_ids()`, which filters brokers by `status == ComponentStatus.RUNNING`. Since no broker ever reaches that state, the method always returns an empty list.

**Impact:**
Any publisher created via `POST /api/publishers` without explicit `broker_ids` calls `_running_broker_ids()`, gets `[]`, and launches with zero broker connections — completely non-functional.

**Reproduction:**
1. `POST /api/brokers` (create a broker)
2. `POST /api/publishers` (no body — relies on auto-detection)
3. Observe the publisher's `broker_ids` is `[]` in `GET /api/state`

**Fix:**
Set `info.status = ComponentStatus.RUNNING` after each successful `self.client.containers.run()` call in `create_broker()`, `create_publisher()`, and `create_subscriber()`. Set `ComponentStatus.STOPPED` in the corresponding remove methods before deleting from `_components`.

---

## Issue #2: No reconciliation between _components and Docker container state

**Severity:** Critical
**File:** `aether/orchestrator/docker_manager.py`

**Description:**
`_components` is a pure in-memory dict that is only updated on explicit create/remove API calls. If a container crashes, is killed externally (`docker kill`), or exits on its own, `_components` still shows it with its last known status. There is no health check, no polling, and no sync mechanism.

**Impact:**
- `GET /api/state` returns stale data — crashed containers appear healthy
- `GET /api/state/topology` draws edges to dead components
- `GET /api/metrics` tries to poll dead containers (silently returns `{}` but still counts them)
- No way for the frontend dashboard to know a component has failed

**Reproduction:**
1. `make demo` (seed the topology)
2. `docker kill aether-broker-1`
3. `GET /api/state` — broker-1 still shows `status: "starting"`

**Fix:**
Add a `_sync_component_status()` helper that iterates `_components`, calls `self.client.containers.get(info.container_id)`, and maps Docker's container status (`"running"`, `"exited"`, `"dead"`, `"created"`) to `ComponentStatus` values. Call it at the top of `get_system_state()`, `get_metrics()`, and `get_topology()`.

---

## Issue #3: Port collisions across component types at high IDs

**Severity:** High
**File:** `aether/orchestrator/docker_manager.py`

**Description:**
Host port formulas for different component types occupy overlapping ranges:

| Component | Formula | Range |
|-----------|---------|-------|
| Broker | `8000 + id * 10` | 8000+ |
| Publisher | `9000 + id * 10` | 9000+ |
| Subscriber | `9100 + id * 10` | 9100+ |

Collisions occur at:
- Broker ID 100 → port 9000 = Publisher ID 0
- Broker ID 110 → port 9100 = Subscriber ID 0
- Publisher ID 10 → port 9100 = Subscriber ID 0

**Impact:**
Docker fails with `"Address already in use"` when the second container tries to bind the same host port. The API returns a generic 500 error with no clear explanation.

**Reproduction:**
1. `POST /api/publishers` (gets ID 1, port 9010)
2. `POST /api/brokers` with `{"broker_id": 101}` → port `8000 + 101*10 = 9010` — collision

**Fix:**
Add a `_check_port_available(port)` helper that scans `_components` for existing `host_port`/`host_status_port` allocations. Call it before `containers.run()` in all three create methods. Raise `ValueError` on conflict so the API returns a clear 400-level error instead of a Docker 500.

---

## Issue #4: WebSocket broadcaster catches bare Exception

**Severity:** Medium
**File:** `aether/orchestrator/events.py`

**Description:**
The `emit()` method catches `Exception` broadly when sending to WebSocket clients:

```python
for ws in self._connections:
    try:
        await ws.send_text(message)
    except Exception:
        dead.add(ws)
```

**Impact:**
- Any non-connection error (e.g., unexpected `TypeError`, `AttributeError`) is silently swallowed
- No logging — impossible to diagnose failures
- A healthy WebSocket client could be incorrectly marked as dead and removed if a non-connection exception occurs

**Fix:**
Narrow the catch to connection-related exceptions (`WebSocketDisconnect`, `RuntimeError`, `ConnectionError`) and add `logger.debug()` for visibility.
