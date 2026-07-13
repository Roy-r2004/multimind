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
  superseded: "superseded",
};

export function MissionStatusBadge({
  status,
}: {
  status: ScrapingMissionStatus | ScrapingBlueprintStatus;
}) {
  return <Badge variant="secondary">{LABELS[status]}</Badge>;
}
