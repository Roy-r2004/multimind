import type { ScrapingEvent, ScrapingExecutionStatus } from "@/lib/scraping/types";

const ACTIVE_STATUSES = new Set<ScrapingExecutionStatus>([
  "queued",
  "running",
  "pause_requested",
  "cancel_requested",
]);

export function isCampaignPollingStatus(status: ScrapingExecutionStatus) {
  return ACTIVE_STATUSES.has(status);
}

export function mergeCampaignEvents(
  current: ScrapingEvent[],
  incoming: ScrapingEvent[],
): ScrapingEvent[] {
  const bySequence = new Map(current.map((event) => [event.sequence_number, event]));
  for (const event of incoming) {
    bySequence.set(event.sequence_number, event);
  }
  return [...bySequence.values()].sort((a, b) => a.sequence_number - b.sequence_number);
}

export function latestCampaignSequence(events: ScrapingEvent[]) {
  return events.reduce((latest, event) => Math.max(latest, event.sequence_number), 0);
}

export function campaignStatusLabel(status: ScrapingExecutionStatus) {
  return status.replaceAll("_", " ");
}
