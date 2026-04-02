import { create } from "zustand";
import type {
  MetricsResponse,
  SystemState,
  TopologyResponse,
  WebSocketEvent,
} from "../api/types";
import * as api from "../api/client";

const MAX_EVENTS = 200;

export interface ChaosState {
  active: boolean;
  targetBrokerId: number | null;
  phase: "triggered" | "declared_dead" | "recovering" | "recovered" | "failed";
  recoveryPath: "replacement" | "redistribution" | null;
}

interface AetherState {
  systemState: SystemState | null;
  topology: TopologyResponse | null;
  metrics: MetricsResponse | null;
  events: WebSocketEvent[];
  wsConnected: boolean;
  loading: boolean;
  chaosState: ChaosState | null;

  fetchState: () => Promise<void>;
  fetchTopology: () => Promise<void>;
  fetchMetrics: () => Promise<void>;
  refreshAll: () => Promise<void>;
  addEvent: (event: WebSocketEvent) => void;
  setWsConnected: (connected: boolean) => void;
  setLoading: (loading: boolean) => void;
  setChaosState: (state: ChaosState) => void;
  setChaosPhase: (phase: ChaosState["phase"], extra?: Partial<ChaosState>) => void;
  clearChaosState: () => void;
}

export const useAetherStore = create<AetherState>((set, get) => ({
  systemState: null,
  topology: null,
  metrics: null,
  events: [],
  wsConnected: false,
  loading: false,
  chaosState: null,

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

  setChaosState: (chaosState) => set({ chaosState }),

  setChaosPhase: (phase, extra) =>
    set((state) => ({
      chaosState: state.chaosState
        ? { ...state.chaosState, ...extra, phase }
        : null,
    })),

  clearChaosState: () => set({ chaosState: null }),
}));
