import { useEffect, useRef } from "react";
import { useAetherStore } from "../store/useAetherStore";
import type { WebSocketEvent, EventType } from "../api/types";


const STRUCTURAL_EVENTS: Set<EventType> = new Set([
  "broker_added",
  "broker_removed",
  "publisher_added",
  "publisher_removed",
  "subscriber_added",
  "subscriber_removed",
  "broker_declared_dead",
  "broker_recovery_started",
  "broker_recovered",
  "broker_recovery_failed",
  "subscriber_reconnected",
]);

const MAX_BACKOFF = 10_000;

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null);
  const backoffRef = useRef(1000);

  const addEvent = useAetherStore((s) => s.addEvent);
  const setWsConnected = useAetherStore((s) => s.setWsConnected);
  const refreshAll = useAetherStore((s) => s.refreshAll);
  const fetchState = useAetherStore((s) => s.fetchState);
  const fetchSnapshots = useAetherStore((s) => s.fetchSnapshots);
  const setChaosPhase = useAetherStore((s) => s.setChaosPhase);
  const clearChaosState = useAetherStore((s) => s.clearChaosState);
  const triggerSnapshotWave = useAetherStore((s) => s.triggerSnapshotWave);

  useEffect(() => {
    let cancelled = false;
    let timeout: ReturnType<typeof setTimeout>;

    function connect() {
      if (cancelled) return;

      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const url = `${protocol}//${window.location.host}/ws/events`;
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        setWsConnected(true);
        backoffRef.current = 1000;
      };

      ws.onmessage = (msg) => {
        try {
          const event: WebSocketEvent = JSON.parse(msg.data);
          addEvent(event);

          if (event.type === "broker_declared_dead") {
            setChaosPhase("declared_dead");
          } else if (event.type === "broker_recovery_started") {
            const path = event.data.recovery_path as "replacement" | "redistribution" | undefined;
            setChaosPhase("recovering", { recoveryPath: path ?? null });
          } else if (event.type === "broker_recovered") {
            setChaosPhase("recovered");
            setTimeout(clearChaosState, 3000);
          } else if (event.type === "broker_recovery_failed") {
            setChaosPhase("failed");
            setTimeout(clearChaosState, 8000);
          } else if (event.type === "snapshot_complete") {
            triggerSnapshotWave();
            fetchSnapshots();
          }

          if (STRUCTURAL_EVENTS.has(event.type)) {
            refreshAll();
          } else if (event.type === "component_status_changed") {
            fetchState();
          }
        } catch {
          // ignore malformed messages
        }
      };

      ws.onclose = () => {
        setWsConnected(false);
        if (!cancelled) {
          timeout = setTimeout(() => {
            backoffRef.current = Math.min(backoffRef.current * 2, MAX_BACKOFF);
            connect();
          }, backoffRef.current);
        }
      };

      ws.onerror = () => {
        ws.close();
      };
    }

    connect();

    return () => {
      cancelled = true;
      clearTimeout(timeout);
      wsRef.current?.close();
    };
  }, [addEvent, setWsConnected, refreshAll, fetchState, fetchSnapshots, setChaosPhase, clearChaosState, triggerSnapshotWave]);
}
