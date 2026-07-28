import type { ScrapingExecutionStatus, ScrapingExecutionSummary } from "@/lib/scraping/types";

const ACTIVE_CAMPAIGN_STATUSES = new Set<ScrapingExecutionStatus>([
  "queued",
  "running",
  "pause_requested",
  "paused",
  "cancel_requested",
]);

export function isMissionCampaignExecution(execution: {
  execution_type?: string | null;
  execution_origin?: string | null;
}): boolean {
  return (
    execution.execution_type === "mission_campaign" ||
    execution.execution_origin === "mission_campaign_mock"
  );
}

export function isMissionCampaignActive(status: ScrapingExecutionStatus): boolean {
  return ACTIVE_CAMPAIGN_STATUSES.has(status);
}

export function pickLatestMissionCampaign(
  campaigns: ScrapingExecutionSummary[],
): ScrapingExecutionSummary | null {
  if (campaigns.length === 0) return null;
  return [...campaigns].sort(
    (left, right) =>
      new Date(right.created_at).getTime() - new Date(left.created_at).getTime(),
  )[0];
}
