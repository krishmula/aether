import { useEffect, useState } from "react";
import { useAetherStore } from "./store/useAetherStore";
import { useWebSocket } from "./hooks/useWebSocket";
import Header from "./components/Header";
import ControlPanel from "./components/ControlPanel";
import TopologyGraph from "./components/TopologyGraph";
import MetricsPanel from "./components/MetricsPanel";
import EventLog from "./components/EventLog";
import SnapshotPanel from "./components/SnapshotPanel";
import ErrorBoundary from "./components/ErrorBoundary";

export default function App() {
  const refreshAll = useAetherStore((s) => s.refreshAll);
  const fetchMetrics = useAetherStore((s) => s.fetchMetrics);
  const fetchSnapshots = useAetherStore((s) => s.fetchSnapshots);
  const [snapshotOpen, setSnapshotOpen] = useState(false);

  useWebSocket();

  useEffect(() => {
    refreshAll();
  }, [refreshAll]);

  useEffect(() => {
    fetchMetrics();
    const id = setInterval(fetchMetrics, 2000);
    return () => clearInterval(id);
  }, [fetchMetrics]);

  useEffect(() => {
    fetchSnapshots();
    const id = setInterval(fetchSnapshots, 30_000);
    return () => clearInterval(id);
  }, [fetchSnapshots]);

  return (
    <>
      <div className="h-screen grid grid-rows-[56px_1fr_240px] grid-cols-[280px_1fr] bg-bg">
        <Header
          snapshotOpen={snapshotOpen}
          onToggleSnapshots={() => setSnapshotOpen((v) => !v)}
        />
        <ErrorBoundary>
          <ControlPanel />
        </ErrorBoundary>
        <ErrorBoundary>
          <TopologyGraph />
        </ErrorBoundary>
        <ErrorBoundary>
          <MetricsPanel />
        </ErrorBoundary>
        <ErrorBoundary>
          <EventLog />
        </ErrorBoundary>
      </div>
      <SnapshotPanel
        isOpen={snapshotOpen}
        onClose={() => setSnapshotOpen(false)}
      />
    </>
  );
}
