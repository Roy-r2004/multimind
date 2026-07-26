import { Badge } from "@/components/ui/badge";
import type { ScrapingBlueprintStatus, ScrapingMissionStatus } from "@/lib/scraping/types";

const LABELS: Record<ScrapingMissionStatus | ScrapingBlueprintStatus, string> = {
  draft: "draft",
  blueprint_generating: "blueprint_generating",
  awaiting_approval: "awaiting_approval",
  approved: "approved",
  rejected: "rejected",
  failed: "failed",
  cancelled: "cancelled",
  generating: "generating",
  queued: "queued",
  running: "running",
  ready_for_review: "ready for review",
  discarded: "discarded",
  superseded: "superseded",
};

export function MissionStatusBadge({
  status,
}: {
  status: ScrapingMissionStatus | ScrapingBlueprintStatus;
}) {
  return <Badge variant="secondary">{LABELS[status]}</Badge>;
}
