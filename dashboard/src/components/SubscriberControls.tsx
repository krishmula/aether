import { useState } from "react";
import type { ComponentInfo } from "../api/types";
import * as api from "../api/client";
import { useAetherStore } from "../store/useAetherStore";
import { partitionPayloadSpace } from "../lib/partition";
import StatusBadge from "./StatusBadge";

interface Props {
  subscribers: ComponentInfo[];
  brokers: ComponentInfo[];
}

export default function SubscriberControls({ subscribers, brokers }: Props) {
  const refreshAll = useAetherStore((s) => s.refreshAll);
  const [brokerId, setBrokerId] = useState<string>("");
  const [rangeLow, setRangeLow] = useState<string>("0");
  const [rangeHigh, setRangeHigh] = useState<string>("255");

  const handleAutoPartition = () => {
    const total = subscribers.length + 1;
    const ranges = partitionPayloadSpace(total);
    const last = ranges[ranges.length - 1];
    setRangeLow(String(last.low));
    setRangeHigh(String(last.high));
  };

  const handleAdd = async () => {
    const bid = parseInt(brokerId);
    if (isNaN(bid)) return;
    try {
      await api.createSubscriber({
        brokerId: bid,
        rangeLow: parseInt(rangeLow),
        rangeHigh: parseInt(rangeHigh),
      });
      await refreshAll();
    } catch (e) {
      console.error("Failed to create subscriber:", e);
    }
  };

  const handleRemove = async (id: number) => {
    try {
      await api.deleteSubscriber(id);
      await refreshAll();
    } catch (e) {
      console.error("Failed to remove subscriber:", e);
    }
  };

  return (
    <section>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-baseline gap-2">
          <h2 className="text-[11px] font-mono font-medium uppercase tracking-[0.08em] text-subscriber">
            Subscribers
          </h2>
          <span className="text-[11px] font-mono text-muted">{subscribers.length}</span>
        </div>
      </div>

      {/* Add form */}
      <div className="flex flex-col gap-2 mb-4">
        <select
          value={brokerId}
          onChange={(e) => setBrokerId(e.target.value)}
          className="bg-elevated border border-border rounded-md px-3 py-2 text-[12px] font-mono text-primary focus:border-border-bright focus:outline-none transition-colors"
        >
          <option value="">broker...</option>
          {brokers.map((b) => (
            <option key={b.component_id} value={b.component_id}>
              B{b.component_id}
            </option>
          ))}
        </select>
        <div className="flex gap-2">
          <input
            type="number"
            min={0}
            max={255}
            value={rangeLow}
            onChange={(e) => setRangeLow(e.target.value)}
            className="w-full bg-elevated border border-border rounded-md px-3 py-2 text-[12px] font-mono text-primary focus:border-border-bright focus:outline-none transition-colors"
            placeholder="low"
          />
          <input
            type="number"
            min={0}
            max={255}
            value={rangeHigh}
            onChange={(e) => setRangeHigh(e.target.value)}
            className="w-full bg-elevated border border-border rounded-md px-3 py-2 text-[12px] font-mono text-primary focus:border-border-bright focus:outline-none transition-colors"
            placeholder="high"
          />
        </div>
        <div className="flex gap-2">
          <button
            onClick={handleAutoPartition}
            className="flex-1 text-[10px] font-mono text-muted hover:text-secondary px-3 py-1.5 rounded-md border border-border hover:border-border-bright transition-colors duration-200"
          >
            auto-partition
          </button>
          <button
            onClick={handleAdd}
            disabled={!brokerId}
            className="flex-1 text-[11px] font-mono font-medium uppercase tracking-[0.06em] px-3 py-1.5 rounded-md bg-primary text-bg hover:bg-secondary transition-all duration-200 disabled:opacity-20"
          >
            Add
          </button>
        </div>
      </div>

      {subscribers.length === 0 ? (
        <p className="text-[12px] text-muted">—</p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {subscribers.map((s) => (
            <li
              key={s.component_id}
              className={`flex items-center justify-between px-3 py-2 rounded-md bg-elevated border border-border text-[12px] transition-colors duration-200 hover:border-border-bright ${
                (s.status === "running" || s.status === "starting") ? "alive-item" : ""
              }`}
              style={{ "--breathe-color": "#e0a643" } as React.CSSProperties}
            >
              <div className="flex items-center gap-2.5">
                <StatusBadge status={s.status} />
                <span className="font-mono text-primary">S{s.component_id}</span>
                <span className="text-muted font-mono text-[10px]">
                  {s.broker_id != null ? `B${s.broker_id}` : "unassigned"} · {s.range_low}–{s.range_high}
                </span>
              </div>
              <button
                onClick={() => handleRemove(s.component_id)}
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
