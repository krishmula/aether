import { useAetherStore } from "../store/useAetherStore";
import BrokerControls from "./BrokerControls";
import PublisherControls from "./PublisherControls";
import SubscriberControls from "./SubscriberControls";
import ChaosControls from "./ChaosControls";

export default function ControlPanel() {
  const systemState = useAetherStore((s) => s.systemState);

  return (
    <aside className="row-span-1 border-r border-border bg-surface overflow-y-auto">
      <div className="p-6 flex flex-col gap-8">
        <BrokerControls brokers={systemState?.brokers ?? []} />
        <PublisherControls
          publishers={systemState?.publishers ?? []}
          brokers={systemState?.brokers ?? []}
        />
        <SubscriberControls
          subscribers={systemState?.subscribers ?? []}
          brokers={systemState?.brokers ?? []}
        />
        <hr className="border-border" />
        <ChaosControls />
      </div>
    </aside>
  );
}
