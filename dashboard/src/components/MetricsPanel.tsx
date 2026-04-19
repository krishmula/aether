import { useAetherStore } from "../store/useAetherStore";

function formatLatency(us: number): string {
  if (us === 0) return "—";
  if (us >= 1000) return `${(us / 1000).toFixed(1)}ms`;
  return `${us.toFixed(0)}us`;
}

function Sparkline({ data }: { data: number[] }) {
  if (data.length < 2) return null;
  const max = Math.max(...data, 1);
  const w = 100;
  const h = 28;
  const points = data
    .map((v, i) => {
      const x = (i / (data.length - 1)) * w;
      const y = h - (v / max) * (h - 2);
      return `${x},${y}`;
    })
    .join(" ");

  return (
    <svg width={w} height={h} className="mt-1 opacity-80">
      <polyline
        points={points}
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        className="text-primary"
      />
    </svg>
  );
}

export default function MetricsPanel() {
  const metrics = useAetherStore((s) => s.metrics);
  const throughput = useAetherStore((s) => s.throughput);
  const throughputHistory = useAetherStore((s) => s.throughputHistory);

  // Aggregate latency across all subscribers (weighted by sample count).
  const aggLatency = (() => {
    if (!metrics?.subscribers?.length) return null;
    const withSamples = metrics.subscribers.filter(
      (s) => s.latency_us.sample_count > 0
    );
    if (!withSamples.length) return null;
    // Use the median of all subscriber p50s as aggregate p50, etc.
    const p50s = withSamples.map((s) => s.latency_us.p50).sort((a, b) => a - b);
    const p99s = withSamples.map((s) => s.latency_us.p99).sort((a, b) => a - b);
    return {
      p50: p50s[Math.floor(p50s.length / 2)],
      p99: p99s[Math.floor(p99s.length / 2)],
    };
  })();

  return (
    <div className="border-r border-border bg-surface p-6 overflow-y-auto flex flex-col">
      <h2 className="text-[11px] font-mono font-medium uppercase tracking-[0.08em] text-secondary mb-5">
        Metrics
      </h2>

      {/* Summary cards */}
      <div className="grid grid-cols-2 gap-3 mb-5">
        <MetricCard
          label="Throughput"
          value={`${Math.round(throughput)}`}
          unit="msg/s"
          color="text-primary"
          sparkline={<Sparkline data={throughputHistory} />}
        />
        <MetricCard
          label="Latency p50"
          value={aggLatency ? formatLatency(aggLatency.p50) : "—"}
          color="text-primary"
        />
        <MetricCard
          label="Messages"
          value={(metrics?.total_messages_processed ?? 0).toLocaleString()}
          color="text-primary"
        />
        <MetricCard
          label="Latency p99"
          value={aggLatency ? formatLatency(aggLatency.p99) : "—"}
          color="text-muted"
        />
        <MetricCard
          label="Brokers"
          value={String(metrics?.total_brokers ?? 0)}
          color="text-broker"
        />
        <MetricCard
          label="Publishers"
          value={String(metrics?.total_publishers ?? 0)}
          color="text-publisher"
        />
      </div>

      {/* Per-broker breakdown */}
      {metrics && metrics.brokers.length > 0 && (
        <div className="mb-5">
          <h3 className="text-[10px] font-mono uppercase tracking-[0.06em] text-muted mb-2">
            Brokers
          </h3>
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
                  <td className="text-right py-1 text-primary">
                    {b.messages_processed.toLocaleString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Subscriber latency breakdown */}
      {metrics && metrics.subscribers && metrics.subscribers.length > 0 && (
        <div className="min-h-0">
          <h3 className="text-[10px] font-mono uppercase tracking-[0.06em] text-muted mb-2">
            Subscriber Latency
          </h3>
          <table className="w-full text-[11px] font-mono">
            <thead>
              <tr className="text-muted">
                <th className="text-left pb-2 font-normal">ID</th>
                <th className="text-right pb-2 font-normal">p50</th>
                <th className="text-right pb-2 font-normal">p95</th>
                <th className="text-right pb-2 font-normal">p99</th>
                <th className="text-right pb-2 font-normal">msgs</th>
              </tr>
            </thead>
            <tbody>
              {metrics.subscribers.map((s) => (
                <tr key={s.subscriber_id} className="text-secondary">
                  <td className="text-subscriber py-1">S{s.subscriber_id}</td>
                  <td className="text-right py-1 text-primary">
                    {formatLatency(s.latency_us.p50)}
                  </td>
                  <td className="text-right py-1">
                    {formatLatency(s.latency_us.p95)}
                  </td>
                  <td className="text-right py-1">
                    {formatLatency(s.latency_us.p99)}
                  </td>
                  <td className="text-right py-1">
                    {s.total_received.toLocaleString()}
                  </td>
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
  unit,
  color,
  sparkline,
}: {
  label: string;
  value: string;
  unit?: string;
  color: string;
  sparkline?: React.ReactNode;
}) {
  return (
    <div className="rounded-lg p-4 border border-border bg-elevated">
      <p className="text-muted text-[10px] font-mono uppercase tracking-[0.06em] mb-1">
        {label}
      </p>
      <p className={`text-2xl font-light font-mono tracking-tight ${color}`}>
        {value}
        {unit && (
          <span className="text-xs text-muted ml-1">{unit}</span>
        )}
      </p>
      {sparkline}
    </div>
  );
}
