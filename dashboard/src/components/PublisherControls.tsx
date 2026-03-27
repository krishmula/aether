import type { ComponentInfo } from "../api/types";
import * as api from "../api/client";
import { useAetherStore } from "../store/useAetherStore";
import StatusBadge from "./StatusBadge";

interface Props {
  publishers: ComponentInfo[];
  brokers: ComponentInfo[];
}

export default function PublisherControls({ publishers, brokers }: Props) {
  const refreshAll = useAetherStore((s) => s.refreshAll);

  const handleAdd = async () => {
    try {
      const brokerIds = brokers.map((b) => b.component_id);
      await api.createPublisher(brokerIds.length > 0 ? { brokerIds } : undefined);
      await refreshAll();
    } catch (e) {
      console.error("Failed to create publisher:", e);
    }
  };

  const handleRemove = async (id: number) => {
    try {
      await api.deletePublisher(id);
      await refreshAll();
    } catch (e) {
      console.error("Failed to remove publisher:", e);
    }
  };

  return (
    <section>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-baseline gap-2">
          <h2 className="text-[11px] font-mono font-medium uppercase tracking-[0.08em] text-publisher">
            Publishers
          </h2>
          <span className="text-[11px] font-mono text-muted">{publishers.length}</span>
        </div>
        <button
          onClick={handleAdd}
          className="text-[11px] font-mono text-muted hover:text-primary transition-colors duration-200"
        >
          + add
        </button>
      </div>
      {publishers.length === 0 ? (
        <p className="text-[12px] text-muted">—</p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {publishers.map((p) => (
            <li
              key={p.component_id}
              className={`flex items-center justify-between px-3 py-2 rounded-md bg-elevated border border-border text-[12px] transition-colors duration-200 hover:border-border-bright ${
                (p.status === "running" || p.status === "starting") ? "alive-item" : ""
              }`}
              style={{ "--breathe-color": "#5ec269" } as React.CSSProperties}
            >
              <div className="flex items-center gap-2.5">
                <StatusBadge status={p.status} />
                <span className="font-mono text-primary">P{p.component_id}</span>
                <span className="text-muted font-mono text-[10px]">
                  → {p.broker_ids.map((id) => `B${id}`).join(" ")}
                </span>
              </div>
              <button
                onClick={() => handleRemove(p.component_id)}
                className="text-muted hover:text-error transition-colors duration-200 text-[10px]"
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
