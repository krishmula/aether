import { useEffect, useState } from "react";
import { useAetherStore } from "../store/useAetherStore";

export default function SnapshotPanel({
  isOpen,
  onClose,
}: {
  isOpen: boolean;
  onClose: () => void;
}) {
  const snapshots = useAetherStore((s) => s.snapshots);
  const throughput = useAetherStore((s) => s.throughput);
  const metrics = useAetherStore((s) => s.metrics);
  const [nowSec, setNowSec] = useState(() => Date.now() / 1000);

  useEffect(() => {
    const id = setInterval(() => setNowSec(Date.now() / 1000), 1000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    if (!isOpen) return;
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [isOpen, onClose]);

  const worstAge = snapshots?.brokers.reduce<number | null>((worst, s) => {
    const age = s.timestamp != null ? nowSec - s.timestamp : null;
    if (age == null) return worst;
    if (worst == null) return age;
    return Math.max(worst, age);
  }, null);

  const healthColor =
    worstAge == null
      ? "bg-muted"
      : worstAge < 30
        ? "bg-[#5ec269]"
        : worstAge < 90
          ? "bg-[#e0a643]"
          : "bg-[#e05252]";

  const avgAge = snapshots?.brokers.reduce((sum, s) => {
    const age = s.timestamp != null ? nowSec - s.timestamp : 0;
    return sum + age;
  }, 0);
  const avgAgeSec =
    snapshots && snapshots.brokers.length > 0
      ? avgAge! / snapshots.brokers.length
      : null;

  const activeBrokers = snapshots?.brokers.length ?? 0;
  const totalBrokers = metrics?.total_brokers ?? 0;

  const throughputStr =
    throughput > 1000
      ? `${(throughput / 1000).toFixed(1)}k`
      : throughput > 0
        ? `${Math.round(throughput)}`
        : "0";

  const avgAgeStr =
    avgAgeSec == null
      ? "—"
      : avgAgeSec < 60
        ? `${Math.round(avgAgeSec)}s`
        : `${Math.round(avgAgeSec / 60)}m`;

  if (!isOpen) return null;

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/30 backdrop-blur-sm z-40"
        onClick={onClose}
      />

      {/* Panel */}
      <div className="fixed right-4 top-[68px] bottom-4 w-[420px] z-50 bg-elevated/95 backdrop-blur-xl border border-border rounded-xl shadow-2xl overflow-hidden flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-border shrink-0">
          <div className="flex items-center gap-2">
            <span className={`inline-block w-2 h-2 rounded-full ${healthColor}`} />
            <h2 className="text-[11px] font-mono font-medium uppercase tracking-[0.08em] text-secondary">
              System Snapshots
            </h2>
          </div>
          <button
            onClick={onClose}
            className="text-muted hover:text-primary transition-colors text-sm leading-none"
          >
            ✕
          </button>
        </div>

        {/* Scrollable content */}
        <div className="flex-1 overflow-y-auto p-6">
          {/* Rate cards */}
          <div className="grid grid-cols-2 gap-3 mb-5">
            <RateCard
              value={throughputStr}
              label="msg/s throughput"
              sub="messages processed per second"
            />
            <RateCard
              value={avgAgeStr}
              label="avg freshness"
              sub="average snapshot age"
            />
            <RateCard
              value={String(activeBrokers)}
              label="active brokers"
              sub={totalBrokers > 0 ? `of ${totalBrokers} total` : "in cluster"}
            />
            <RateCard
              value={
                snapshots
                  ? snapshots.brokers
                    .reduce((s, b) => s + b.seen_message_count, 0)
                    .toLocaleString()
                  : "0"
              }
              label="snapshot msgs"
              sub="total seen across brokers"
            />
          </div>

          {/* Snapshot table */}
          {snapshots && snapshots.brokers.length > 0 ? (
            <table className="w-full text-[11px] font-mono">
              <thead>
                <tr className="text-muted border-b border-border">
                  <th className="text-left pb-2 font-normal">broker</th>
                  <th className="text-right pb-2 font-normal">age</th>
                  <th className="text-right pb-2 font-normal">subs</th>
                  <th className="text-right pb-2 font-normal">msgs</th>
                  <th className="text-right pb-2 font-normal">state</th>
                  <th className="text-right pb-2 font-normal w-24">health</th>
                </tr>
              </thead>
              <tbody>
                {snapshots.brokers.map((s) => {
                  const ageSec = s.timestamp != null ? nowSec - s.timestamp : null;
                  const ageStr =
                    ageSec == null
                      ? "—"
                      : ageSec < 60
                        ? `${Math.round(ageSec)}s`
                        : `${Math.round(ageSec / 60)}m`;
                  const ageColor =
                    ageSec == null
                      ? "text-muted"
                      : ageSec < 30
                        ? "text-[#5ec269]"
                        : ageSec < 90
                          ? "text-[#e0a643]"
                          : "text-[#e05252]";
                  const msgStr =
                    s.seen_message_count > 1000
                      ? `${(s.seen_message_count / 1000).toFixed(1)}k`
                      : String(s.seen_message_count);

                  const healthPct =
                    ageSec == null
                      ? 0
                      : ageSec < 10
                        ? 100
                        : ageSec < 30
                          ? 90
                          : ageSec < 60
                            ? 75
                            : ageSec < 90
                              ? 50
                              : 25;
                  const healthBarColor =
                    healthPct >= 75
                      ? "bg-[#5ec269]"
                      : healthPct >= 50
                        ? "bg-[#e0a643]"
                        : "bg-[#e05252]";

                  return (
                    <tr key={s.broker_id} className="text-secondary">
                      <td className="text-[#6899f7] py-1.5">B{s.broker_id}</td>
                      <td className={`text-right py-1.5 ${ageColor}`}>
                        <span className="inline-block w-1.5 h-1.5 rounded-full mr-1.5 align-middle" style={{ backgroundColor: ageSec == null ? "#4a4a4a" : ageSec < 30 ? "#5ec269" : ageSec < 90 ? "#e0a643" : "#e05252" }} />
                        {ageStr}
                      </td>
                      <td className="text-right py-1.5">{s.subscriber_count}</td>
                      <td className="text-right py-1.5">{msgStr}</td>
                      <td className="text-right py-1.5 text-muted">{s.snapshot_state}</td>
                      <td className="text-right py-1.5">
                        <div className="flex items-center justify-end gap-2">
                          <div className="w-16 h-1 rounded-full bg-elevated overflow-hidden">
                            <div
                              className={`h-full rounded-full ${healthBarColor}`}
                              style={{ width: `${healthPct}%` }}
                            />
                          </div>
                          <span className="text-muted w-8 text-right">{healthPct}%</span>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          ) : snapshots ? (
            <p className="text-[11px] text-muted font-mono text-center py-4">no snapshots yet</p>
          ) : (
            <p className="text-[11px] text-muted font-mono text-center py-4">loading...</p>
          )}
        </div>
      </div>
    </>
  );
}

function RateCard({
  value,
  label,
  sub,
}: {
  value: string;
  label: string;
  sub: string;
}) {
  return (
    <div className="rounded-lg p-4 border border-border bg-elevated">
      <p className="text-2xl font-light font-mono tracking-tight text-primary mb-1">
        {value}
      </p>
      <p className="text-muted text-[10px] font-mono uppercase tracking-[0.06em]">
        {label}
      </p>
      <p className="text-muted text-[9px] font-mono mt-0.5 opacity-60">
        {sub}
      </p>
    </div>
  );
}
