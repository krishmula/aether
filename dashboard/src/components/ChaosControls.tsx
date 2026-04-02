import { useAetherStore } from "../store/useAetherStore";
import * as api from "../api/client";
import { useState } from "react";

export default function ChaosControls() {
  const systemState = useAetherStore((s) => s.systemState);
  const chaosState = useAetherStore((s) => s.chaosState);
  const setChaosState = useAetherStore((s) => s.setChaosState);
  const [apiError, setApiError] = useState<string | null>(null);

  const runningBrokers = (systemState?.brokers ?? []).filter(
    (b) => b.status === "running",
  );
  const canChaos = runningBrokers.length >= 2 && !chaosState?.active;

  const handleChaos = async () => {
    setApiError(null);
    try {
      const res = await api.createChaos();
      setChaosState({
        active: true,
        targetBrokerId: res.chaos_target,
        phase: "triggered",
        recoveryPath: null,
      });
    } catch (e) {
      const message = e instanceof Error ? e.message : "Unknown error occurred";
      setApiError(message);
      console.error("Failed to create chaos:", e);
    }
  };

  const pathLabel =
    chaosState?.recoveryPath === "replacement"
      ? "Path A — Snapshot Restore"
      : chaosState?.recoveryPath === "redistribution"
        ? "Path B — Redistribution"
        : null;

  const pathColor =
    chaosState?.recoveryPath === "replacement" ? "text-broker" : "text-subscriber";

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <span className="text-[10px] font-mono font-semibold uppercase tracking-widest text-muted">
          Chaos
        </span>
        <button
          onClick={handleChaos}
          disabled={!canChaos}
          className="text-[10px] font-mono font-medium uppercase tracking-wider px-2 py-1 rounded bg-error/10 text-error border border-error/20 hover:bg-error/20 transition-colors duration-150 disabled:opacity-30 disabled:cursor-not-allowed"
        >
          Create Chaos
        </button>
      </div>

      {apiError && (
        <div className="mt-2 text-[10px] font-mono text-error">
          Failed: {apiError}
        </div>
      )}

      {chaosState && (
        <div className="mt-2 space-y-1">
          <div className="flex items-center gap-1.5">
            <span
              className={`w-1.5 h-1.5 rounded-full ${
                chaosState.phase === "recovered"
                  ? "bg-publisher"
                  : chaosState.phase === "failed"
                    ? "bg-error animate-pulse"
                    : chaosState.phase === "recovering"
                      ? "bg-starting animate-pulse"
                      : "bg-error animate-pulse"
              }`}
            />
            <span className="text-[10px] font-mono text-secondary">
              {chaosState.phase === "triggered" && `B${chaosState.targetBrokerId} targeted`}
              {chaosState.phase === "declared_dead" && `B${chaosState.targetBrokerId} declared dead`}
              {chaosState.phase === "recovering" && `Recovering B${chaosState.targetBrokerId}`}
              {chaosState.phase === "recovered" && `B${chaosState.targetBrokerId} recovered`}
              {chaosState.phase === "failed" && `Recovery failed for B${chaosState.targetBrokerId}`}
            </span>
          </div>

          {pathLabel && (
            <div className={`text-[10px] font-mono ${pathColor} pl-3`}>
              ↳ {pathLabel}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
