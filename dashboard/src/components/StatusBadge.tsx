import type { ComponentStatus } from "../api/types";

const STATUS_STYLES: Record<ComponentStatus, string> = {
  running: "bg-publisher",
  starting: "bg-starting animate-pulse",
  stopping: "bg-subscriber",
  stopped: "bg-muted",
  error: "bg-error",
};

export default function StatusBadge({ status }: { status: ComponentStatus }) {
  return (
    <span
      className={`w-1.5 h-1.5 rounded-full ${STATUS_STYLES[status]}`}
      title={status}
    />
  );
}
