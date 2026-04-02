import { useEffect, useRef } from "react";
import { useAetherStore } from "../store/useAetherStore";
import type { EventType } from "../api/types";

const EVENT_COLORS: Partial<Record<EventType, string>> = {
  broker_added: "text-broker",
  broker_removed: "text-broker",
  publisher_added: "text-publisher",
  publisher_removed: "text-publisher",
  subscriber_added: "text-subscriber",
  subscriber_removed: "text-subscriber",
  component_status_changed: "text-starting",
  broker_declared_dead: "text-error",
  broker_recovery_started: "text-error",
  broker_recovered: "text-publisher",
  subscriber_reconnected: "text-subscriber",
  message_published: "text-muted",
  message_delivered: "text-muted",
  snapshot_initiated: "text-error",
  snapshot_complete: "text-error",
  peer_joined: "text-secondary",
  peer_evicted: "text-secondary",
};

function formatTime(ts: number): string {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString("en-US", { hour12: false });
}

function summarize(type: EventType, data: Record<string, unknown>): string {
  const name = data.container_name ?? data.hostname ?? "";
  switch (type) {
    case "broker_added":
      return `broker ${data.component_id} created`;
    case "broker_removed":
      return `broker ${data.component_id} removed`;
    case "publisher_added":
      return `publisher ${data.component_id} created`;
    case "publisher_removed":
      return `publisher ${data.component_id} removed`;
    case "subscriber_added":
      return `subscriber ${data.component_id} created`;
    case "subscriber_removed":
      return `subscriber ${data.component_id} removed`;
    case "component_status_changed":
      return `${name} → ${data.status}`;
    case "peer_joined":
      return `peer joined: ${name}`;
    case "peer_evicted":
      return `peer evicted: ${name}`;
    case "broker_declared_dead":
      return `broker ${data.broker_id ?? data.component_id ?? "?"} declared dead`;
    case "broker_recovery_started":
      return `recovery started: ${data.recovery_path ?? ""}`;
    case "broker_recovered":
      return `broker ${data.broker_id ?? data.component_id ?? "?"} recovered (${data.recovery_path ?? "?"})`;
    case "subscriber_reconnected":
      return `subscriber ${data.subscriber_id ?? data.component_id ?? "?"} reconnected`;
    default:
      return type.replace(/_/g, " ");
  }
}

export default function EventLog() {
  const events = useAetherStore((s) => s.events);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: 0 });
  }, [events.length]);

  return (
    <div className="bg-surface p-6 overflow-hidden flex flex-col">
      <h2 className="text-[11px] font-mono font-medium uppercase tracking-[0.08em] text-secondary mb-5 shrink-0">
        Event Log
      </h2>
      <div ref={scrollRef} className="overflow-y-auto flex-1 min-h-0">
        {events.length === 0 ? (
          <p className="text-[12px] text-muted font-mono">waiting for events...</p>
        ) : (
          <ul className="flex flex-col gap-px">
            {events.map((e, i) => (
              <li
                key={`${e.timestamp}-${i}`}
                className="flex gap-4 text-[11px] font-mono py-1 border-b border-border last:border-0"
              >
                <span className="text-muted shrink-0 tabular-nums">
                  {formatTime(e.timestamp)}
                </span>
                <span className={EVENT_COLORS[e.type] ?? "text-muted"}>
                  {summarize(e.type, e.data)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
