import { useEffect, useState } from "react";
import { useAetherStore } from "../store/useAetherStore";

export default function MetricsPanel() {
  const metrics = useAetherStore((s) => s.metrics);
  const snapshots = useAetherStore((s) => s.snapshots);
  const [snapshotExpanded, setSnapshotExpanded] = useState(true);
  const [nowSec, setNowSec] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const id = setInterval(() => setNowSec(Date.now() / 1000), 1000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="border-r border-border bg-surface p-6 overflow-y-auto flex flex-col">
      <h2 className="text-[11px] font-mono font-medium uppercase tracking-[0.08em] text-secondary mb-5">
        Metrics
      </h2>

      {/* Summary cards */}
      <div className="grid grid-cols-2 gap-3 mb-5">
        <MetricCard
          label="Messages"
          value={metrics?.total_messages_processed ?? 0}
          color="text-primary"
        />
        <MetricCard
          label="Brokers"
          value={metrics?.total_brokers ?? 0}
          color="text-broker"
        />
        <MetricCard
          label="Publishers"
          value={metrics?.total_publishers ?? 0}
          color="text-publisher"
        />
        <MetricCard
          label="Subscribers"
          value={metrics?.total_subscribers ?? 0}
          color="text-subscriber"
        />
      </div>

      {/* Per-broker breakdown */}
      {metrics && metrics.brokers.length > 0 && (
        <div className="min-h-0">
          <table className="w-full text-[11px] font-mono">
            <thead>
              <tr className="text-muted">
                <th className="text-left pb-2 font-normal">ID</th>
                <th className="text-right pb-2 font-normal">peers</th>
                <th className="text-right pb-2 font-normal">subs</th>
                <th className="text-right pb-2 font-normal">msgs</th>
              </tr>
            </thead>
            <tbody>
              {metrics.brokers.map((b) => (
                <tr key={b.broker_id} className="text-secondary">
                  <td className="text-broker py-1">B{b.broker_id}</td>
                  <td className="text-right py-1">{b.peer_count}</td>
                  <td className="text-right py-1">{b.subscriber_count}</td>
                  <td className="text-right py-1 text-primary">{b.messages_processed.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Snapshot inspector */}
      <div className="mt-5 border-t border-border pt-4">
        <button
          className="w-full flex items-center justify-between text-[11px] font-mono uppercase tracking-[0.08em] text-secondary mb-3"
          onClick={() => setSnapshotExpanded(v => !v)}
        >
          <span>Snapshots</span>
          <span className="text-muted">{snapshotExpanded ? "▾" : "▸"}</span>
        </button>

        {snapshotExpanded && snapshots && (
          <table className="w-full text-[11px] font-mono">
            <thead>
              <tr className="text-muted">
                <th className="text-left pb-2 font-normal">broker</th>
                <th className="text-right pb-2 font-normal">age</th>
                <th className="text-right pb-2 font-normal">subs</th>
                <th className="text-right pb-2 font-normal">msgs</th>
                <th className="text-right pb-2 font-normal">state</th>
              </tr>
            </thead>
            <tbody>
              {snapshots.brokers.map(s => {
                const ageSec = s.timestamp != null ? nowSec - s.timestamp : null;
                const ageStr = ageSec == null ? "—"
                  : ageSec < 60 ? `${Math.round(ageSec)}s`
                  : `${Math.round(ageSec / 60)}m`;
                const ageClass = ageSec == null ? "text-muted"
                  : ageSec < 30 ? "text-[#5ec269]"
                  : ageSec < 90 ? "text-[#e0a643]"
                  : "text-[#e05252]";
                const msgStr = s.seen_message_count > 1000
                  ? `${(s.seen_message_count / 1000).toFixed(1)}k`
                  : String(s.seen_message_count);
                return (
                  <tr key={s.broker_id} className="text-secondary">
                    <td className="text-[#6899f7] py-1">B{s.broker_id}</td>
                    <td className={`text-right py-1 ${ageClass}`}>{ageStr}</td>
                    <td className="text-right py-1">{s.subscriber_count}</td>
                    <td className="text-right py-1">{msgStr}</td>
                    <td className="text-right py-1 text-muted">{s.snapshot_state}</td>
                  </tr>
                );
              })}
              {snapshots.brokers.length === 0 && (
                <tr>
                  <td colSpan={5} className="text-muted py-2 text-center">no snapshots yet</td>
                </tr>
              )}
            </tbody>
          </table>
        )}

        {snapshotExpanded && !snapshots && (
          <p className="text-[11px] text-muted font-mono">loading...</p>
        )}
      </div>
    </div>
  );
}

function MetricCard({
  label,
  value,
  color,
}: {
  label: string;
  value: number;
  color: string;
}) {
  return (
    <div className="rounded-lg p-4 border border-border bg-elevated">
      <p className="text-muted text-[10px] font-mono uppercase tracking-[0.06em] mb-1">{label}</p>
      <p className={`text-2xl font-light font-mono tracking-tight ${color}`}>
        {value.toLocaleString()}
      </p>
    </div>
  );
}
