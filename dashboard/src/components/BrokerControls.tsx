import type { ComponentInfo } from "../api/types";
import * as api from "../api/client";
import { useAetherStore } from "../store/useAetherStore";
import StatusBadge from "./StatusBadge";

interface Props {
  brokers: ComponentInfo[];
}

export default function BrokerControls({ brokers }: Props) {
  const refreshAll = useAetherStore((s) => s.refreshAll);

  const handleAdd = async () => {
    try {
      await api.createBroker();
      await refreshAll();
    } catch (e) {
      console.error("Failed to create broker:", e);
    }
  };

  const handleRemove = async (id: number) => {
    try {
      await api.deleteBroker(id);
      await refreshAll();
    } catch (e) {
      console.error("Failed to remove broker:", e);
    }
  };

  return (
    <section>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-baseline gap-2">
          <h2 className="text-[11px] font-mono font-medium uppercase tracking-[0.08em] text-broker">
            Brokers
          </h2>
          <span className="text-[11px] font-mono text-muted">{brokers.length}</span>
        </div>
        <button
          onClick={handleAdd}
          className="text-[11px] font-mono text-muted hover:text-primary transition-colors duration-200"
        >
          + add
        </button>
      </div>
      {brokers.length === 0 ? (
        <p className="text-[12px] text-muted">—</p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {brokers.map((b) => (
            <li
              key={b.component_id}
              className={`flex items-center justify-between px-3 py-2 rounded-md bg-elevated border border-border text-[12px] transition-colors duration-200 hover:border-border-bright ${
                (b.status === "running" || b.status === "starting") ? "alive-item" : ""
              }`}
              style={{ "--breathe-color": "#6899f7" } as React.CSSProperties}
            >
              <div className="flex items-center gap-2.5">
                <StatusBadge status={b.status} />
                <span className="font-mono text-primary">B{b.component_id}</span>
              </div>
              <button
                onClick={() => handleRemove(b.component_id)}
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
