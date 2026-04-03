import { useAetherStore } from "../store/useAetherStore";
import * as api from "../api/client";

export default function Header({
  snapshotOpen,
  onToggleSnapshots,
}: {
  snapshotOpen: boolean;
  onToggleSnapshots: () => void;
}) {
  const wsConnected = useAetherStore((s) => s.wsConnected);
  const loading = useAetherStore((s) => s.loading);
  const setLoading = useAetherStore((s) => s.setLoading);
  const refreshAll = useAetherStore((s) => s.refreshAll);
  const snapshots = useAetherStore((s) => s.snapshots);

  const handleSeed = async () => {
    setLoading(true);
    try {
      await api.seedDemo();
      await refreshAll();
    } catch (e) {
      console.error("Seed failed:", e);
    } finally {
      setLoading(false);
    }
  };

  const nowSec = Date.now() / 1000;
  const worstAge = snapshots?.brokers.reduce<number | null>((worst, s) => {
    const age = s.timestamp != null ? nowSec - s.timestamp : null;
    if (age == null) return worst;
    if (worst == null) return age;
    return Math.max(worst, age);
  }, null);

  const dotColor =
    worstAge == null
      ? "bg-muted"
      : worstAge < 30
        ? "bg-[#5ec269]"
        : worstAge < 90
          ? "bg-[#e0a643]"
          : "bg-[#e05252]";

  const brokerCount = snapshots?.brokers.length ?? 0;
  const hasData = brokerCount > 0;

  return (
    <header className="col-span-2 flex items-center justify-between px-8 border-b border-border bg-surface">
      <div className="flex items-center gap-6">
        <h1 className="text-[15px] font-light tracking-[-0.02em] text-primary">
          <span className="font-medium">aether</span>
          <span className="text-muted mx-2">/</span>
          <span className="text-secondary">control plane</span>
        </h1>
      </div>

      <div className="flex items-center gap-5">
        <span className="flex items-center gap-2 text-[11px] font-mono text-muted tracking-wide">
          <span
            className={`w-1.5 h-1.5 rounded-full transition-colors duration-500 ${
              wsConnected ? "bg-publisher" : "bg-error"
            }`}
          />
          {wsConnected ? "LIVE" : "OFFLINE"}
        </span>

        <button
          onClick={onToggleSnapshots}
          className={`group relative px-4 py-2 text-[11px] font-mono font-medium uppercase tracking-[0.08em] rounded transition-all duration-200 flex items-center gap-2 ${
            snapshotOpen
              ? "border-2 border-primary text-primary bg-primary/10"
              : hasData
                ? "border-2 border-[#5ec269]/50 text-primary bg-[#5ec269]/8 hover:bg-[#5ec269]/15 hover:border-[#5ec269]/70 shadow-[0_0_16px_rgba(94,194,105,0.2)] hover:shadow-[0_0_20px_rgba(94,194,105,0.3)]"
                : "border-2 border-border text-secondary hover:text-primary hover:border-border-bright"
          }`}
          title="View system snapshot health and metrics"
        >
          <span
            className={`w-2 h-2 rounded-full ${dotColor} ${
              hasData && !snapshotOpen ? "animate-pulse" : ""
            }`}
          />
          <span>View Snapshots</span>
          {hasData && !snapshotOpen && (
            <span className="text-[9px] text-[#5ec269] ml-0.5">
              {brokerCount} active
            </span>
          )}
          <span className="text-muted text-[9px] ml-1">
            {snapshotOpen ? "▾" : "▸"}
          </span>
        </button>

        <button
          onClick={handleSeed}
          disabled={loading}
          className="px-4 py-1.5 text-[11px] font-mono font-medium uppercase tracking-[0.08em] rounded bg-primary text-bg hover:bg-secondary transition-all duration-200 disabled:opacity-30"
        >
          {loading ? "Seeding..." : "Seed Demo"}
        </button>
      </div>
    </header>
  );
}
