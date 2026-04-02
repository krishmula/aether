import { useEffect } from "react";
import { useAetherStore } from "./store/useAetherStore";
import { useWebSocket } from "./hooks/useWebSocket";
import Header from "./components/Header";
import ControlPanel from "./components/ControlPanel";
import TopologyGraph from "./components/TopologyGraph";
import MetricsPanel from "./components/MetricsPanel";
import EventLog from "./components/EventLog";
import ErrorBoundary from "./components/ErrorBoundary";

export default function App() {
  const refreshAll = useAetherStore((s) => s.refreshAll);
  const fetchMetrics = useAetherStore((s) => s.fetchMetrics);

  useWebSocket();

  useEffect(() => {
    refreshAll();
  }, [refreshAll]);

  useEffect(() => {
    fetchMetrics();
    const id = setInterval(fetchMetrics, 2000);
    return () => clearInterval(id);
  }, [fetchMetrics]);

  return (
    <div className="h-screen grid grid-rows-[56px_1fr_240px] grid-cols-[280px_1fr] bg-bg">
      <Header />
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
  );
}
