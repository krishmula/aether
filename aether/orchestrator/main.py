"""FastAPI control-plane for Aether — Phase 2.4."""

from __future__ import annotations

import asyncio
import logging
import random

from aether.utils.log import BoundLogger, setup_logging

import docker.errors
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from aether.core.payload_range import partition_payload_space
from aether.core.uint8 import UInt8

from .docker_manager import DockerManager
from .events import EventBroadcaster
from .health import HealthMonitor
from .models import (
    ComponentResponse,
    ComponentStatus,
    ComponentType,
    CreateBrokerRequest,
    CreatePublisherRequest,
    CreateSubscriberRequest,
    EventType,
    MetricsResponse,
    SnapshotStatusResponse,
    SnapshotsResponse,
    SystemState,
    TopologyResponse,
    TriggerSnapshotRequest,
)
from .recovery import RecoveryManager
from .bootstrap_client import deregister_from_bootstrap
from .settings import settings
from . import metrics

setup_logging(
    level=settings.log_level,
    json_console=True,
    # Ships orchestrator logs to the OTel Collector when OTEL_ENDPOINT is set
    # in the environment (configured in docker-compose.yml for the orchestrator
    # service). No-op when the env var is absent — local dev is unaffected.
    otel_endpoint=settings.otel_endpoint,
    service_name="aether-orchestrator",
)
logger = BoundLogger(
    logging.getLogger(__name__),
    {"service_name": "aether-orchestrator", "component_type": "orchestrator"},
)

app = FastAPI(title="Aether Control Plane")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

docker_mgr = DockerManager()
broadcaster = EventBroadcaster()
recovery_mgr = RecoveryManager(docker_mgr, broadcaster, settings)


# ---------------------------------------------------------------------------
# Subscriber Reconnect
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Broker lifecycle
# ---------------------------------------------------------------------------


@app.post("/api/brokers", response_model=ComponentResponse)
async def add_broker(
    req: CreateBrokerRequest = CreateBrokerRequest(),
) -> ComponentResponse:
    """Spin up a new broker container."""
    try:
        info = docker_mgr.create_broker(req)
    except docker.errors.APIError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    await broadcaster.emit(EventType.BROKER_ADDED, info.model_dump(mode="json"))
    return ComponentResponse(action="created", component=info)


@app.delete("/api/brokers/{broker_id}", response_model=ComponentResponse)
async def remove_broker(broker_id: int) -> ComponentResponse:
    """Stop and remove a broker container, then reassign its subscribers."""
    try:
        info = docker_mgr.remove_broker(broker_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except docker.errors.APIError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # Deregister from bootstrap so peer lists stay clean
    await deregister_from_bootstrap(info.hostname, info.internal_port, settings)

    await broadcaster.emit(EventType.BROKER_REMOVED, info.model_dump(mode="json"))
    await recovery_mgr.reassign_orphans(broker_id)
    return ComponentResponse(action="removed", component=info)


# ---------------------------------------------------------------------------
# Publisher lifecycle
# ---------------------------------------------------------------------------


@app.post("/api/publishers", response_model=ComponentResponse)
async def add_publisher(
    req: CreatePublisherRequest = CreatePublisherRequest(),
) -> ComponentResponse:
    """Spin up a new publisher container."""
    try:
        info = docker_mgr.create_publisher(req)
    except docker.errors.APIError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    await broadcaster.emit(EventType.PUBLISHER_ADDED, info.model_dump(mode="json"))
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
    await broadcaster.emit(EventType.PUBLISHER_REMOVED, info.model_dump(mode="json"))
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
    await broadcaster.emit(EventType.SUBSCRIBER_ADDED, info.model_dump(mode="json"))
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
    await broadcaster.emit(EventType.SUBSCRIBER_REMOVED, info.model_dump(mode="json"))
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


@app.get("/metrics")
async def prometheus_metrics() -> Response:
    """Prometheus scrape endpoint — returns all metrics in Prometheus text format.

    Prometheus polls this every 15 seconds (configured in observability/prometheus.yaml).
    The response uses CONTENT_TYPE_LATEST so Prometheus knows how to parse it.
    generate_latest() reads from the global in-process registry, which is the same
    registry that metrics.py, recovery.py, and the background updater all write to.
    """
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ---------------------------------------------------------------------------
# Snapshots (deferred — Phase 5.6)
# ---------------------------------------------------------------------------


@app.post("/api/snapshots", response_model=SnapshotStatusResponse)
async def trigger_snapshot(
    req: TriggerSnapshotRequest = TriggerSnapshotRequest(),
) -> SnapshotStatusResponse:
    """Trigger a Chandy-Lamport snapshot. (Not yet implemented — Phase 5.6)"""
    raise HTTPException(
        status_code=501, detail="Snapshot triggering not yet implemented"
    )


@app.get("/api/snapshots", response_model=SnapshotsResponse)
async def get_snapshots() -> SnapshotsResponse:
    """Return per-broker snapshot metadata by querying peer status servers."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, docker_mgr.get_snapshots)


# ---------------------------------------------------------------------------
# Demo seed
# ---------------------------------------------------------------------------


@app.post("/api/seed")
async def seed_demo() -> dict:
    """Spin up the default demo topology: 3 brokers, 2 publishers, 3 subscribers.
    Idempotent — checks current state and only creates missing components.
    """
    try:
        state = docker_mgr.get_system_state()
        created: list = []

        # Brokers: bring total up to 3
        broker_ids = [b.component_id for b in state.brokers]
        while len(broker_ids) < 3:
            info = docker_mgr.create_broker(CreateBrokerRequest())
            await broadcaster.emit(EventType.BROKER_ADDED, info.model_dump(mode="json"))
            broker_ids.append(info.component_id)
            created.append(info)

        # Publishers: bring total up to 2
        publishers_needed = 2 - len(state.publishers)
        for _ in range(max(0, publishers_needed)):
            info = docker_mgr.create_publisher(
                CreatePublisherRequest(broker_ids=broker_ids)
            )
            await broadcaster.emit(
                EventType.PUBLISHER_ADDED, info.model_dump(mode="json")
            )
            created.append(info)

        # Subscribers: one per broker, bring total up to 3
        subscribers_needed = 3 - len(state.subscribers)
        seed_ranges = partition_payload_space(UInt8(3))
        for i, broker_id in enumerate(broker_ids[: max(0, subscribers_needed)]):
            r = seed_ranges[i]
            info = docker_mgr.create_subscriber(
                CreateSubscriberRequest(
                    broker_id=broker_id, range_low=int(r.low), range_high=int(r.high)
                )
            )
            await broadcaster.emit(
                EventType.SUBSCRIBER_ADDED, info.model_dump(mode="json")
            )
            created.append(info)

    except docker.errors.APIError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "seeded": len(created),
        "components": [c.model_dump(mode="json") for c in created],
    }


# ---------------------------------------------------------------------------
# Chaos
# ---------------------------------------------------------------------------


@app.post("/api/chaos")
async def create_chaos() -> dict:
    """Force-kill a random running broker to simulate an unplanned crash.

    Picks a random running broker (requires at least 2), SIGKILLs its
    container, then immediately triggers the recovery pipeline — bypassing the
    health monitor's polling delay while producing identical recovery behavior.
    """
    running_brokers = [
        info
        for info in docker_mgr._components.values()
        if info.component_type == ComponentType.BROKER
        and info.status == ComponentStatus.RUNNING
    ]
    if len(running_brokers) < 2:
        raise HTTPException(
            status_code=409,
            detail="Need at least 2 running brokers to create chaos",
        )

    target = random.choice(running_brokers)
    try:
        docker_mgr.force_kill_broker(target.component_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    asyncio.create_task(
        recovery_mgr.handle_broker_dead(
            target.component_id,
            target.hostname,
            target.internal_port,
        )
    )
    return {"chaos_target": target.component_id}


# ---------------------------------------------------------------------------
# Health monitoring & recovery
# ---------------------------------------------------------------------------


@app.on_event("startup")
async def start_health_monitoring() -> None:
    health_monitor = HealthMonitor(
        components=docker_mgr._components,
        on_broker_dead=recovery_mgr.handle_broker_dead,
        poll_interval=settings.health_broker_poll_interval,
        failure_threshold=settings.health_broker_failure_threshold,
        startup_grace_seconds=settings.health_broker_startup_grace_seconds,
        request_timeout=settings.health_broker_request_timeout,
    )
    asyncio.create_task(health_monitor.run())
    asyncio.create_task(_metrics_updater())
    asyncio.create_task(_snapshot_monitor())


async def _snapshot_monitor() -> None:
    """Poll broker snapshot states via direct /status queries.

    Queries each broker's /status endpoint directly for its latest local
    snapshot. Emits SNAPSHOT_COMPLETE when a broker's snapshot timestamp
    advances. Seeds last_timestamps at startup so pre-existing snapshots
    are not replayed as new events.
    """
    last_timestamps: dict[str, float] = {}
    loop = asyncio.get_event_loop()

    def _running_brokers():
        return [
            info
            for info in docker_mgr._components.values()
            if info.component_type == ComponentType.BROKER
            and info.status == ComponentStatus.RUNNING
        ]

    # Seed baseline so pre-existing snapshots are not treated as new on startup.
    for info in _running_brokers():
        try:
            raw = await loop.run_in_executor(
                None,
                docker_mgr._fetch_status,
                info.hostname,
                info.internal_status_port,
            )
            snap = raw.get("latest_snapshot")
            if snap and snap.get("timestamp"):
                last_timestamps[info.hostname] = snap["timestamp"]
        except Exception:
            pass

    while True:
        await asyncio.sleep(settings.snapshot_monitor_poll_interval)
        for info in _running_brokers():
            try:
                raw = await loop.run_in_executor(
                    None,
                    docker_mgr._fetch_status,
                    info.hostname,
                    info.internal_status_port,
                )
                snap = raw.get("latest_snapshot")
                if not snap or not snap.get("timestamp"):
                    continue

                ts = snap["timestamp"]
                prev = last_timestamps.get(info.hostname)
                if prev is None or ts > prev:
                    last_timestamps[info.hostname] = ts
                    await broadcaster.emit(
                        EventType.SNAPSHOT_COMPLETE,
                        {
                            "broker_id": info.component_id,
                            "broker_address": f"{info.hostname}:{info.internal_port}",
                            "snapshot_id": snap["snapshot_id"],
                            "snapshot_timestamp": ts,
                        },
                    )
            except Exception:
                pass


async def _metrics_updater() -> None:
    """Background task that authoritatively samples live metrics.

    This is the only path allowed to refresh orchestrator metrics from live
    broker/subscriber status endpoints. ``GET /api/metrics`` serves the latest
    cached snapshot produced here and must remain a passive read.

    Counter delta strategy for messages_published_total:
      Broker /status returns a cumulative count since that container started.
      If a broker container restarts, its count resets to 0 — a naive approach
      would subtract that reset and produce a large negative delta. We only
      ever increment by a positive delta, so restarts cause a small gap rather
      than a bogus negative spike.
    """
    # Track per-broker message totals from the last poll so we can compute deltas.
    # Keyed by broker_id (int). Populated on first successful poll.
    last_broker_totals: dict[int, int] = {}

    # Track which (component_type, component_id) label sets were active in the
    # previous poll. Any that vanish from _components between polls were deleted
    # and must be explicitly zeroed — otherwise the gauge retains its last value
    # of 1 indefinitely and the running-count panels never decrease.
    _seen_components: set[tuple[str, str]] = set()

    _POLL_INTERVAL = 1.0
    loop = asyncio.get_running_loop()

    while True:
        try:
            broker_metrics = await loop.run_in_executor(
                None, docker_mgr.refresh_metrics_cache
            )

            # --- Component up/down gauges -----------------------------------
            current_components: set[tuple[str, str]] = set()
            for info in docker_mgr._components.values():
                key = (info.component_type.value, str(info.component_id))
                current_components.add(key)
                is_up = 1 if info.status == ComponentStatus.RUNNING else 0
                metrics.component_up.labels(
                    component_type=info.component_type.value,
                    component_id=str(info.component_id),
                ).set(is_up)

            # Zero out any components removed since the last poll.
            for component_type, component_id in _seen_components - current_components:
                metrics.component_up.labels(
                    component_type=component_type,
                    component_id=component_id,
                ).set(0)
            _seen_components = current_components

            # --- Broker-level gauges + message counter ----------------------
            for bm in broker_metrics.brokers:
                bid = str(bm.broker_id)
                metrics.broker_peer_count.labels(broker_id=bid).set(bm.peer_count)
                metrics.broker_subscriber_count.labels(broker_id=bid).set(
                    bm.subscriber_count
                )

                # Only increment by positive delta — guards against counter
                # resets when a broker container is restarted.
                prev = last_broker_totals.get(bm.broker_id, 0)
                delta = bm.messages_processed - prev
                if delta > 0:
                    metrics.messages_published_total.inc(delta)
                # Always update last-known value, even on reset, so the next
                # poll computes the delta relative to the new baseline.
                last_broker_totals[bm.broker_id] = bm.messages_processed

        except Exception:
            # Non-fatal — a temporary Docker socket error or a broker being
            # mid-restart shouldn't break the metrics loop. The next iteration
            # will pick up fresh values.
            logger.debug("metrics updater poll failed (non-critical)")
        await asyncio.sleep(_POLL_INTERVAL)


@app.get("/api/assignments")
async def get_assignments() -> dict:
    """Return current subscriber → broker mappings."""
    assignments = [
        {
            "subscriber_id": info.component_id,
            "broker_id": info.broker_id,
        }
        for info in docker_mgr._components.values()
        if info.component_type == ComponentType.SUBSCRIBER
        and info.broker_id is not None
    ]
    return {"assignments": assignments}


@app.get("/api/assignment")
async def get_assignment(subscriber_id: int) -> dict:
    """Return the broker currently assigned to a subscriber.

    Used by subscribers during reconnection: after detecting their broker is
    dead (via Ping/Pong timeout), they query this endpoint to find where to
    reconnect. Returns 404 if the subscriber is unknown or its assigned broker
    is not yet running (subscriber should retry with backoff).
    """
    subscriber = next(
        (
            info
            for info in docker_mgr._components.values()
            if info.component_type == ComponentType.SUBSCRIBER
            and info.component_id == subscriber_id
        ),
        None,
    )
    if subscriber is None or subscriber.broker_id is None:
        raise HTTPException(status_code=404, detail="subscriber not found")

    broker = next(
        (
            info
            for info in docker_mgr._components.values()
            if info.component_type == ComponentType.BROKER
            and info.component_id == subscriber.broker_id
        ),
        None,
    )
    if broker is None or broker.status not in (
        ComponentStatus.RUNNING,
        ComponentStatus.STARTING,
    ):
        raise HTTPException(status_code=404, detail="broker not available")

    return {
        "subscriber_id": subscriber_id,
        "broker_host": broker.hostname,
        "broker_port": broker.internal_port,
    }


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
