import { useAetherStore } from "../store/useAetherStore";

export default function MetricsPanel() {
  const metrics = useAetherStore((s) => s.metrics);

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
