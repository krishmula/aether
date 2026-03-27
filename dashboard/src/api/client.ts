import type {
  ComponentResponse,
  MetricsResponse,
  SystemState,
  TopologyResponse,
} from "./types";

const BASE = "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status}: ${body}`);
  }
  return res.json();
}

// --- System state ---

export const getState = () => request<SystemState>("/state");
export const getTopology = () => request<TopologyResponse>("/state/topology");
export const getMetrics = () => request<MetricsResponse>("/metrics");

// --- Broker lifecycle ---

export const createBroker = (brokerId?: number) =>
  request<ComponentResponse>("/brokers", {
    method: "POST",
    body: JSON.stringify(brokerId != null ? { broker_id: brokerId } : {}),
  });

export const deleteBroker = (id: number) =>
  request<ComponentResponse>(`/brokers/${id}`, { method: "DELETE" });

// --- Publisher lifecycle ---

export const createPublisher = (opts?: {
  publisherId?: number;
  brokerIds?: number[];
  interval?: number;
}) =>
  request<ComponentResponse>("/publishers", {
    method: "POST",
    body: JSON.stringify({
      ...(opts?.publisherId != null && { publisher_id: opts.publisherId }),
      ...(opts?.brokerIds && { broker_ids: opts.brokerIds }),
      ...(opts?.interval != null && { interval: opts.interval }),
    }),
  });

export const deletePublisher = (id: number) =>
  request<ComponentResponse>(`/publishers/${id}`, { method: "DELETE" });

// --- Subscriber lifecycle ---

export const createSubscriber = (opts: {
  subscriberId?: number;
  brokerId: number;
  rangeLow: number;
  rangeHigh: number;
}) =>
  request<ComponentResponse>("/subscribers", {
    method: "POST",
    body: JSON.stringify({
      ...(opts.subscriberId != null && { subscriber_id: opts.subscriberId }),
      broker_id: opts.brokerId,
      range_low: opts.rangeLow,
      range_high: opts.rangeHigh,
    }),
  });

export const deleteSubscriber = (id: number) =>
  request<ComponentResponse>(`/subscribers/${id}`, { method: "DELETE" });

// --- Demo seed ---

export const seedDemo = () =>
  request<{ seeded: number; components: unknown[] }>("/seed", {
    method: "POST",
  });
