import { useAetherStore } from "../store/useAetherStore";
import * as api from "../api/client";

export default function Header() {
  const wsConnected = useAetherStore((s) => s.wsConnected);
  const loading = useAetherStore((s) => s.loading);
  const setLoading = useAetherStore((s) => s.setLoading);
  const refreshAll = useAetherStore((s) => s.refreshAll);

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
