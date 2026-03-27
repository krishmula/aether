import { create } from "zustand";
import type {
  MetricsResponse,
  SystemState,
  TopologyResponse,
  WebSocketEvent,
} from "../api/types";
import * as api from "../api/client";

const MAX_EVENTS = 200;

interface AetherState {
  systemState: SystemState | null;
  topology: TopologyResponse | null;
  metrics: MetricsResponse | null;
  events: WebSocketEvent[];
  wsConnected: boolean;
  loading: boolean;

  fetchState: () => Promise<void>;
  fetchTopology: () => Promise<void>;
  fetchMetrics: () => Promise<void>;
  refreshAll: () => Promise<void>;
  addEvent: (event: WebSocketEvent) => void;
  setWsConnected: (connected: boolean) => void;
  setLoading: (loading: boolean) => void;
}

export const useAetherStore = create<AetherState>((set, get) => ({
  systemState: null,
  topology: null,
  metrics: null,
  events: [],
  wsConnected: false,
  loading: false,

  fetchState: async () => {
    try {
      const systemState = await api.getState();
      set({ systemState });
    } catch (e) {
      console.error("Failed to fetch state:", e);
    }
  },

  fetchTopology: async () => {
    try {
      const topology = await api.getTopology();
      set({ topology });
    } catch (e) {
      console.error("Failed to fetch topology:", e);
    }
  },

  fetchMetrics: async () => {
    try {
      const metrics = await api.getMetrics();
      set({ metrics });
    } catch (e) {
      console.error("Failed to fetch metrics:", e);
    }
  },

  refreshAll: async () => {
    const { fetchState, fetchTopology } = get();
    await Promise.all([fetchState(), fetchTopology()]);
  },

  addEvent: (event) =>
    set((state) => ({
      events: [event, ...state.events].slice(0, MAX_EVENTS),
    })),

  setWsConnected: (wsConnected) => set({ wsConnected }),
  setLoading: (loading) => set({ loading }),
}));
