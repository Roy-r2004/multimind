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
  approved: "border-teal-300/40 bg-teal-400/10 text-teal-100",
  rejected: "border-rose-400/40 bg-rose-500/10 text-rose-100",
  failed: "border-rose-400/40 bg-rose-500/10 text-rose-100",
  awaiting_approval: "border-sky-300/40 bg-sky-400/15 text-sky-100",
  blueprint_generating: "border-sky-300/40 bg-sky-400/15 text-sky-100 dream-twinkle",
  generating: "border-sky-300/40 bg-sky-400/15 text-sky-100 dream-twinkle",
  cancelled: "border-white/20 bg-white/5 text-white/60",
  superseded: "border-white/15 bg-white/5 text-white/50",
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
        TONE[status] ?? "border-white/15 bg-white/5 text-white/70",
      )}
    >
      {LABELS[status]}
    </span>
  );
}
