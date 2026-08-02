import type { MapsCensusRunStatus } from "@/lib/maps/types";
import { cn } from "@/lib/utils";

const LABELS: Record<MapsCensusRunStatus, string> = {
  queued: "queued",
  running: "running",
  completed: "completed",
  completed_with_warnings: "completed w/ warnings",
  failed: "failed",
  cancelled: "cancelled",
};

const TONE: Record<MapsCensusRunStatus, string> = {
  queued: "border-border bg-muted/50 text-muted-foreground",
  running: "border-primary/30 bg-primary/10 text-primary dream-twinkle",
  completed: "border-teal-300/50 bg-teal-50 text-teal-800",
  completed_with_warnings: "border-amber-300/50 bg-amber-50 text-amber-900",
  failed: "border-rose-300/50 bg-rose-50 text-rose-800",
  cancelled: "border-border bg-muted text-muted-foreground",
};

export function MapsRunStatusBadge({ status }: { status: MapsCensusRunStatus | string }) {
  const known = (status in LABELS ? status : "running") as MapsCensusRunStatus;
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2.5 py-0.5 text-[10px] font-medium uppercase tracking-[0.14em]",
        TONE[known],
      )}
    >
      {LABELS[known]}
    </span>
  );
}
