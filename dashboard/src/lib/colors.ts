import type { ComponentStatus, ComponentType, EdgeType } from "../api/types";

export const NODE_COLORS: Record<ComponentType, string> = {
  bootstrap: "#555555",
  broker: "#6899f7",
  publisher: "#5ec269",
  subscriber: "#e0a643",
};

export const STATUS_COLORS: Record<ComponentStatus, string> = {
  starting: "#a78bfa",
  running: "#5ec269",
  stopping: "#e0a643",
  stopped: "#4a4a4a",
  error: "#e05252",
};

export const EDGE_COLORS: Record<EdgeType, string> = {
  peer: "#4a6a9f",
  publish: "#3d7a44",
  subscribe: "#9a7530",
};
