// Mirrors aether/orchestrator/models.py

export type ComponentType = "bootstrap" | "broker" | "publisher" | "subscriber";
export type ComponentStatus = "starting" | "running" | "stopping" | "stopped" | "error";
export type EdgeType = "peer" | "publish" | "subscribe";

export type EventType =
  | "broker_added"
  | "broker_removed"
  | "publisher_added"
  | "publisher_removed"
  | "subscriber_added"
  | "subscriber_removed"
  | "component_status_changed"
  | "broker_declared_dead"
  | "broker_recovery_started"
  | "broker_recovered"
  | "broker_recovery_failed"
  | "subscriber_reconnected"
  | "message_published"
  | "message_delivered"
  | "snapshot_initiated"
  | "snapshot_complete"
  | "peer_joined"
  | "peer_evicted";

export interface ComponentInfo {
  component_type: ComponentType;
  component_id: number;
  container_name: string;
  container_id: string | null;
  hostname: string;
  internal_port: number;
  internal_status_port: number;
  host_port: number | null;
  host_status_port: number | null;
  status: ComponentStatus;
  created_at: string;
  broker_ids: number[];
  publish_interval: number;
  broker_id: number | null;
  range_low: number | null;
  range_high: number | null;
}

export interface ComponentResponse {
  action: string;
  component: ComponentInfo;
}

export interface BootstrapInfo {
  host: string;
  port: number;
  status_port: number;
  registered_brokers: string[];
  broker_count: number;
  uptime_seconds: number | null;
}

export interface SystemState {
  brokers: ComponentInfo[];
  publishers: ComponentInfo[];
  subscribers: ComponentInfo[];
  bootstrap: BootstrapInfo | null;
}

export interface TopologyNode {
  id: string;
  component_type: ComponentType;
  component_id: number;
  status: ComponentStatus;
}

export interface TopologyEdge {
  source: string;
  target: string;
  edge_type: EdgeType;
}

export interface TopologyResponse {
  nodes: TopologyNode[];
  edges: TopologyEdge[];
}

export interface BrokerMetrics {
  broker_id: number;
  host: string;
  port: number;
  peer_count: number;
  subscriber_count: number;
  messages_processed: number;
  uptime_seconds: number;
  snapshot_state: string;
}

export interface MetricsResponse {
  brokers: BrokerMetrics[];
  total_messages_processed: number;
  total_brokers: number;
  total_publishers: number;
  total_subscribers: number;
}

export interface WebSocketEvent {
  type: EventType;
  data: Record<string, unknown>;
  timestamp: number;
}

export interface BrokerSnapshotInfo {
  broker_id: number;
  broker_address: string;
  snapshot_id: string | null;
  timestamp: number | null;
  age_seconds: number | null;
  peer_count: number;
  subscriber_count: number;
  seen_message_count: number;
  snapshot_state: string;
}

export interface SnapshotsResponse {
  brokers: BrokerSnapshotInfo[];
  fetched_at: number;
}
