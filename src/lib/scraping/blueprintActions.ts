import type { ScrapingBlueprint, ScrapingBlueprintStatus } from "@/lib/scraping/types";

export function canApproveBlueprint(status: ScrapingBlueprintStatus) {
  return status === "ready_for_review";
}

export function canRejectBlueprint(status: ScrapingBlueprintStatus) {
  return status === "ready_for_review";
}

export function canRequestBlueprintChanges(status: ScrapingBlueprintStatus) {
  return status === "ready_for_review" || status === "approved" || status === "failed";
}

export function canEditBlueprint(status: ScrapingBlueprintStatus) {
  return status === "draft" || status === "ready_for_review";
}

export function canRegenerateBlueprint(status: ScrapingBlueprintStatus) {
  return status !== "queued" && status !== "running";
}

export function canDiscardBlueprint(status: ScrapingBlueprintStatus) {
  return status === "draft" || status === "ready_for_review";
}

export function validRevisionInstruction(value: string) {
  return Boolean(value.trim());
}

export function parseStructuredBlueprint(value: string): Record<string, unknown> | null {
  try {
    const parsed: unknown = JSON.parse(value);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

export function activeApprovedBlueprint(
  blueprints: ScrapingBlueprint[],
  activeBlueprintId?: string | null,
) {
  return blueprints.find(
    (blueprint) => blueprint.id === activeBlueprintId && blueprint.status === "approved",
  );
}

export function scrapingCtaMessage(active: ScrapingBlueprint | undefined) {
  return active ? "Campaign execution will be added in Phase 2." : "Approval is required.";
}
