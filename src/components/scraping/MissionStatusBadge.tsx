import type { ScrapingBlueprintStatus, ScrapingMissionStatus } from "@/lib/scraping/types";
import { cn } from "@/lib/utils";

const LABELS: Record<ScrapingMissionStatus | ScrapingBlueprintStatus, string> = {
  draft: "draft",
  blueprint_generating: "charting",
  awaiting_approval: "awaiting chart",
  approved: "approved",
  rejected: "rejected",
  failed: "failed",
  cancelled: "cancelled",
  generating: "generating",
  superseded: "superseded",
};

const TONE: Partial<Record<ScrapingMissionStatus | ScrapingBlueprintStatus, string>> = {
  approved: "border-teal-300/50 bg-teal-50 text-teal-800",
  rejected: "border-rose-300/50 bg-rose-50 text-rose-800",
  failed: "border-rose-300/50 bg-rose-50 text-rose-800",
  awaiting_approval: "border-primary/30 bg-primary/10 text-primary",
  blueprint_generating: "border-primary/30 bg-primary/10 text-primary dream-twinkle",
  generating: "border-primary/30 bg-primary/10 text-primary dream-twinkle",
  cancelled: "border-border bg-muted text-muted-foreground",
  superseded: "border-border bg-muted/60 text-muted-foreground",
};

export function MissionStatusBadge({
  status,
}: {
  status: ScrapingMissionStatus | ScrapingBlueprintStatus;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2.5 py-0.5 text-[10px] font-medium uppercase tracking-[0.14em]",
        TONE[status] ?? "border-border bg-muted/50 text-muted-foreground",
      )}
    >
      {LABELS[status]}
    </span>
  );
}
