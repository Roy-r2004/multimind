import type { ScrapingBlueprint } from "@/lib/scraping/types";

const CACHE_PREFIX = "multiai:generated-scraping-blueprint:";

function cacheKey(missionId: string): string {
  return `${CACHE_PREFIX}${missionId}`;
}

export function cacheGeneratedScrapingBlueprint(
  missionId: string,
  blueprint: ScrapingBlueprint,
): void {
  if (typeof window === "undefined") {
    return;
  }

  try {
    window.sessionStorage.setItem(cacheKey(missionId), JSON.stringify(blueprint));
  } catch {
    // The server remains the source of truth if browser storage is unavailable.
  }
}

export function readCachedGeneratedScrapingBlueprint(missionId: string): ScrapingBlueprint | null {
  if (typeof window === "undefined") {
    return null;
  }

  try {
    const raw = window.sessionStorage.getItem(cacheKey(missionId));
    if (!raw) {
      return null;
    }

    const parsed = JSON.parse(raw) as Partial<ScrapingBlueprint>;
    if (
      typeof parsed.id !== "string" ||
      typeof parsed.mission_id !== "string" ||
      parsed.mission_id !== missionId ||
      typeof parsed.version !== "number" ||
      typeof parsed.status !== "string"
    ) {
      window.sessionStorage.removeItem(cacheKey(missionId));
      return null;
    }

    return parsed as ScrapingBlueprint;
  } catch {
    window.sessionStorage.removeItem(cacheKey(missionId));
    return null;
  }
}

export function clearCachedGeneratedScrapingBlueprint(missionId: string): void {
  if (typeof window === "undefined") {
    return;
  }

  try {
    window.sessionStorage.removeItem(cacheKey(missionId));
  } catch {
    // Nothing else is required; the cached value is only a navigation handoff.
  }
}
