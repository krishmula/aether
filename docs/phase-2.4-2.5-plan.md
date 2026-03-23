# Plan: Phase 2.4 + 2.5 — FastAPI Endpoints & Event Broadcasting

## Context

`DockerManager` is complete. Now we wire it up to a FastAPI app (`main.py`) and create
the WebSocket broadcaster (`events.py`) that pushes real-time events to the dashboard.
These two ship together — `main.py` imports `EventBroadcaster` directly.

One bug also needs fixing in `docker_manager.py` before the API goes live.

---

## File 1: `aether/orchestrator/events.py` (CREATE)

```python
"""WebSocket event broadcaster for the Aether orchestrator."""

from __future__ import annotations

import json
import time

from fastapi import WebSocket


class EventBroadcaster:
    """Manages connected WebSocket clients and broadcasts events to all of them."""

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()

    def register(self, ws: WebSocket) -> None:
        self._connections.add(ws)

    def unregister(self, ws: WebSocket) -> None:
        self._connections.discard(ws)

    async def emit(self, event_type, data: dict) -> None:
        """Broadcast a JSON event to all connected clients.

        Callers pass an EventType enum member; .value converts it to a string.
        Dead connections are silently removed.
        """
        message = json.dumps(
            {
                "type": event_type.value,
                "data": data,
                "timestamp": time.time(),
            }
        )
        dead: set[WebSocket] = set()
        for ws in self._connections:
            try:
                await ws.send_text(message)
            except Exception:
                dead.add(ws)
        self._connections -= dead
```

---

## File 2: `aether/orchestrator/main.py` (CREATE)

```python
"""FastAPI control-plane for Aether — Phase 2.4."""

from __future__ import annotations

import logging

import docker.errors
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .docker_manager import DockerManager
from .events import EventBroadcaster
from .models import (
    ComponentResponse,
    CreateBrokerRequest,
    CreatePublisherRequest,
    CreateSubscriberRequest,
    EventType,
    MetricsResponse,
    SnapshotStatusResponse,
    SystemState,
    TopologyResponse,
    TriggerSnapshotRequest,
)

logger = logging.getLogger(__name__)

app = FastAPI(title="Aether Control Plane")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

docker_mgr = DockerManager()
broadcaster = EventBroadcaster()


# ---------------------------------------------------------------------------
# Broker lifecycle
# ---------------------------------------------------------------------------


@app.post("/api/brokers", response_model=ComponentResponse)
async def add_broker(req: CreateBrokerRequest = CreateBrokerRequest()) -> ComponentResponse:
    """Spin up a new broker container."""
    try:
        info = docker_mgr.create_broker(req)
    except docker.errors.APIError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    await broadcaster.emit(EventType.BROKER_ADDED, info.model_dump())
    return ComponentResponse(action="created", component=info)


@app.delete("/api/brokers/{broker_id}", response_model=ComponentResponse)
async def remove_broker(broker_id: int) -> ComponentResponse:
    """Stop and remove a broker container."""
    try:
        info = docker_mgr.remove_broker(broker_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except docker.errors.APIError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    await broadcaster.emit(EventType.BROKER_REMOVED, info.model_dump())
    return ComponentResponse(action="removed", component=info)


# ---------------------------------------------------------------------------
# Publisher lifecycle
# ---------------------------------------------------------------------------


@app.post("/api/publishers", response_model=ComponentResponse)
async def add_publisher(req: CreatePublisherRequest = CreatePublisherRequest()) -> ComponentResponse:
    """Spin up a new publisher container."""
    try:
        info = docker_mgr.create_publisher(req)
    except docker.errors.APIError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    await broadcaster.emit(EventType.PUBLISHER_ADDED, info.model_dump())
    return ComponentResponse(action="created", component=info)


@app.delete("/api/publishers/{publisher_id}", response_model=ComponentResponse)
async def remove_publisher(publisher_id: int) -> ComponentResponse:
    """Stop and remove a publisher container."""
    try:
        info = docker_mgr.remove_publisher(publisher_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except docker.errors.APIError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    await broadcaster.emit(EventType.PUBLISHER_REMOVED, info.model_dump())
    return ComponentResponse(action="removed", component=info)


# ---------------------------------------------------------------------------
# Subscriber lifecycle
# ---------------------------------------------------------------------------


@app.post("/api/subscribers", response_model=ComponentResponse)
async def add_subscriber(req: CreateSubscriberRequest) -> ComponentResponse:
    """Spin up a new subscriber container."""
    try:
        info = docker_mgr.create_subscriber(req)
    except docker.errors.APIError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    await broadcaster.emit(EventType.SUBSCRIBER_ADDED, info.model_dump())
    return ComponentResponse(action="created", component=info)


@app.delete("/api/subscribers/{subscriber_id}", response_model=ComponentResponse)
async def remove_subscriber(subscriber_id: int) -> ComponentResponse:
    """Stop and remove a subscriber container."""
    try:
        info = docker_mgr.remove_subscriber(subscriber_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except docker.errors.APIError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    await broadcaster.emit(EventType.SUBSCRIBER_REMOVED, info.model_dump())
    return ComponentResponse(action="removed", component=info)


# ---------------------------------------------------------------------------
# System state
# ---------------------------------------------------------------------------


@app.get("/api/state", response_model=SystemState)
async def get_state() -> SystemState:
    """Return full system state — all components and bootstrap info."""
    return docker_mgr.get_system_state()


@app.get("/api/state/topology", response_model=TopologyResponse)
async def get_topology() -> TopologyResponse:
    """Return nodes and edges for graph visualization."""
    return docker_mgr.get_topology()


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


@app.get("/api/metrics", response_model=MetricsResponse)
async def get_metrics() -> MetricsResponse:
    """Aggregate metrics from all broker /status endpoints."""
    return docker_mgr.get_metrics()


# ---------------------------------------------------------------------------
# Snapshots (deferred — Phase 5.6)
# ---------------------------------------------------------------------------


@app.post("/api/snapshots", response_model=SnapshotStatusResponse)
async def trigger_snapshot(
    req: TriggerSnapshotRequest = TriggerSnapshotRequest(),
) -> SnapshotStatusResponse:
    """Trigger a Chandy-Lamport snapshot. (Not yet implemented — Phase 5.6)"""
    raise HTTPException(status_code=501, detail="Snapshot triggering not yet implemented")


@app.get("/api/snapshots/latest", response_model=SnapshotStatusResponse)
async def get_latest_snapshot() -> SnapshotStatusResponse:
    """Return the most recent snapshot result. (Not yet implemented — Phase 5.6)"""
    raise HTTPException(status_code=501, detail="Snapshot history not yet implemented")


# ---------------------------------------------------------------------------
# Demo seed
# ---------------------------------------------------------------------------


@app.post("/api/seed")
async def seed_demo() -> dict:
    """Spin up the default demo topology: 3 brokers, 2 publishers, 3 subscribers.
    Idempotent — checks current state and only creates missing components.
    """
    state = docker_mgr.get_system_state()
    created: list = []

    # Brokers: bring total up to 3
    broker_ids = [b.component_id for b in state.brokers]
    while len(broker_ids) < 3:
        info = docker_mgr.create_broker(CreateBrokerRequest())
        await broadcaster.emit(EventType.BROKER_ADDED, info.model_dump())
        broker_ids.append(info.component_id)
        created.append(info)

    # Publishers: bring total up to 2
    publishers_needed = 2 - len(state.publishers)
    for _ in range(max(0, publishers_needed)):
        info = docker_mgr.create_publisher(CreatePublisherRequest(broker_ids=broker_ids))
        await broadcaster.emit(EventType.PUBLISHER_ADDED, info.model_dump())
        created.append(info)

    # Subscribers: one per broker, bring total up to 3
    subscribers_needed = 3 - len(state.subscribers)
    for broker_id in broker_ids[:max(0, subscribers_needed)]:
        info = docker_mgr.create_subscriber(CreateSubscriberRequest(broker_id=broker_id))
        await broadcaster.emit(EventType.SUBSCRIBER_ADDED, info.model_dump())
        created.append(info)

    return {"seeded": len(created), "components": [c.model_dump() for c in created]}


# ---------------------------------------------------------------------------
# WebSocket — real-time event stream
# ---------------------------------------------------------------------------


@app.websocket("/ws/events")
async def websocket_events(websocket: WebSocket) -> None:
    """Stream real-time orchestration events to the dashboard."""
    await websocket.accept()
    broadcaster.register(websocket)
    try:
        while True:
            await websocket.receive_text()  # keep-alive; client may send pings
    except WebSocketDisconnect:
        broadcaster.unregister(websocket)
```

---

## File 3: `aether/orchestrator/docker_manager.py` (FIX)

**Bug:** `BootstrapInfo` is constructed in `get_system_state()` but missing from the import block.

Add `BootstrapInfo` to the existing `.models` import at line 13:

```python
from .models import (
    BootstrapInfo,       # ← add this
    BrokerMetrics,
    ComponentInfo,
    ComponentStatus,
    ComponentType,
    CreateBrokerRequest,
    CreatePublisherRequest,
    CreateSubscriberRequest,
    MetricsResponse,
    SnapshotStatusResponse,
    SystemState,
    TopologyEdge,
    TopologyNode,
    TopologyResponse,
)
```

---

## Verification

```bash
# Set required env vars and start the server
BOOTSTRAP_HOST=localhost BOOTSTRAP_PORT=5000 AETHER_IMAGE=aether:dev \
DOCKER_NETWORK=aether-net uvicorn aether.orchestrator.main:app --reload
```

| Step | Request | Expected |
|------|---------|----------|
| 1 | `GET /docs` | All 13 routes visible in Swagger UI |
| 2 | `POST /api/brokers` | 200, ComponentResponse returned |
| 3 | `GET /api/state` | 200, broker appears in brokers list |
| 4 | `DELETE /api/brokers/1` | 200, ComponentResponse returned |
| 5 | `DELETE /api/brokers/99` | 404 |
| 6 | `POST /api/seed` | 200, 8 components created |
| 7 | `POST /api/seed` (again) | 200, `seeded: 0` (idempotent) |
| 8 | `GET /api/metrics` | 200, broker metrics populated |
| 9 | `GET /api/state/topology` | 200, nodes and edges present |
| 10 | `POST /api/snapshots` | 501 |
| 11 | WS `/ws/events` + `POST /api/brokers` | Event pushed over WebSocket |
