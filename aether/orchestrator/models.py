"""Pydantic models for the Aether orchestrator REST API and WebSocket events."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

# --- Enums ---


class ComponentType(StrEnum):
    BOOTSTRAP = "bootstrap"
    BROKER = "broker"
    PUBLISHER = "publisher"
    SUBSCRIBER = "subscriber"


class ComponentStatus(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


class EventType(StrEnum):
    # Orchestration events
    BROKER_ADDED = "broker_added"
    BROKER_REMOVED = "broker_removed"
    PUBLISHER_ADDED = "publisher_added"
    PUBLISHER_REMOVED = "publisher_removed"
    SUBSCRIBER_ADDED = "subscriber_added"
    SUBSCRIBER_REMOVED = "subscriber_removed"
    COMPONENT_STATUS_CHANGED = "component_status_changed"

    # Failover events
    BROKER_DECLARED_DEAD = "broker_declared_dead"
    BROKER_RECOVERY_STARTED = "broker_recovery_started"
    BROKER_RECOVERED = "broker_recovered"
    BROKER_RECOVERY_FAILED = "broker_recovery_failed"
    SUBSCRIBER_RECONNECTED = "subscriber_reconnected"

    # System events (from log tailing)
    MESSAGE_PUBLISHED = "message_published"
    MESSAGE_DELIVERED = "message_delivered"
    SNAPSHOT_INITIATED = "snapshot_initiated"
    SNAPSHOT_COMPLETE = "snapshot_complete"
    PEER_JOINED = "peer_joined"
    PEER_EVICTED = "peer_evicted"


# --- Component State Tracking ---


class ComponentInfo(BaseModel):
    """What the orchestrator tracks per managed container."""

    component_type: ComponentType
    component_id: int
    container_name: str  # "aether-broker-4"
    container_id: str | None = None  # Docker hex ID, None before run returns
    hostname: str  # Docker DNS name on aether-net, e.g. "broker-4"
    internal_port: int  # TCP port inside the container
    internal_status_port: int  # HTTP status port (internal_port + 10000)
    host_port: int | None = None  # mapped to host for external access
    host_status_port: int | None = None
    status: ComponentStatus = ComponentStatus.STARTING
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # Publisher-specific
    broker_ids: list[int] = Field(default_factory=list)
    publish_interval: float = 1.0

    # Subscriber-specific
    broker_id: int | None = None  # single parent broker
    range_low: int | None = None  # payload range [0-255]
    range_high: int | None = None


# --- API Request Models ---


class CreateBrokerRequest(BaseModel):
    broker_id: int | None = None  # auto-assigned if omitted


class CreatePublisherRequest(BaseModel):
    publisher_id: int | None = None
    broker_ids: list[int] | None = None  # None = all running brokers
    interval: float = Field(default=1.0, gt=0)


class CreateSubscriberRequest(BaseModel):
    subscriber_id: int | None = None
    broker_id: int  # required — subscriber needs exactly one broker
    range_low: int = Field(ge=0, le=255)
    range_high: int = Field(ge=0, le=255)


class TriggerSnapshotRequest(BaseModel):
    initiator_broker_id: int | None = None  # None = pick first running broker


# --- API Response Models ---


class ComponentResponse(BaseModel):
    action: str  # "created", "removed", "error"
    component: ComponentInfo


class BootstrapInfo(BaseModel):
    """Read-only — bootstrap is never created/destroyed by orchestrator."""

    host: str
    port: int
    status_port: int
    registered_brokers: list[str] = Field(default_factory=list)
    broker_count: int = 0
    uptime_seconds: float | None = None


class SystemState(BaseModel):
    brokers: list[ComponentInfo] = Field(default_factory=list)
    publishers: list[ComponentInfo] = Field(default_factory=list)
    subscribers: list[ComponentInfo] = Field(default_factory=list)
    bootstrap: BootstrapInfo | None = None


class TopologyNode(BaseModel):
    id: str  # "broker-1", "publisher-0"
    component_type: ComponentType
    component_id: int
    status: ComponentStatus


class TopologyEdge(BaseModel):
    source: str  # node id
    target: str  # node id
    edge_type: str  # "peer", "publish", "subscribe"


class TopologyResponse(BaseModel):
    nodes: list[TopologyNode] = Field(default_factory=list)
    edges: list[TopologyEdge] = Field(default_factory=list)


class BrokerMetrics(BaseModel):
    """Fields match broker /status JSON keys."""

    broker_id: int
    host: str
    port: int
    peer_count: int = 0
    subscriber_count: int = 0
    messages_processed: int = 0
    uptime_seconds: float = 0.0
    snapshot_state: str = "idle"  # "idle" | "recording"


class LatencyPercentiles(BaseModel):
    p50: float = 0
    p95: float = 0
    p99: float = 0
    sample_count: int = 0


class SubscriberMetrics(BaseModel):
    subscriber_id: int
    broker_id: int | None = None
    total_received: int = 0
    latency_us: LatencyPercentiles = Field(default_factory=LatencyPercentiles)


class MetricsResponse(BaseModel):
    brokers: list[BrokerMetrics] = Field(default_factory=list)
    subscribers: list[SubscriberMetrics] = Field(default_factory=list)
    total_messages_processed: int = 0
    total_brokers: int = 0
    total_publishers: int = 0
    total_subscribers: int = 0
    throughput_msgs_per_sec: float = 0
    topology_generation: int = 0
    fetched_at: float = 0
    sample_interval_seconds: float = 0


class SnapshotStatusResponse(BaseModel):
    snapshot_id: str | None = None
    initiator_broker_id: int | None = None
    timestamp: float | None = None
    broker_states: list[dict] = Field(default_factory=list)
    status: str = "none"  # "none", "in_progress", "complete"


class BrokerSnapshotInfo(BaseModel):
    broker_id: int
    broker_address: str
    snapshot_id: str | None = None
    timestamp: float | None = None
    age_seconds: float | None = None
    peer_count: int = 0
    subscriber_count: int = 0
    seen_message_count: int = 0
    snapshot_state: str = "idle"  # "idle" | "recording"


class SnapshotsResponse(BaseModel):
    brokers: list[BrokerSnapshotInfo] = Field(default_factory=list)
    fetched_at: float


# --- WebSocket Event ---


class WebSocketEvent(BaseModel):
    event_type: EventType
    timestamp: float  # time.time()
    data: dict  # ComponentInfo.model_dump() or event-specific payload
