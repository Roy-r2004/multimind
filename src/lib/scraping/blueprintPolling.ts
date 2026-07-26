import { isAbortError, isRequestCancelled } from "@/lib/api/client";
import type { ScrapingBlueprint, ScrapingBlueprintStatus } from "@/lib/scraping/types";

const TERMINAL_STATUSES = new Set<ScrapingBlueprintStatus>([
  "draft",
  "ready_for_review",
  "approved",
  "rejected",
  "discarded",
  "superseded",
  "failed",
]);

export function isBlueprintPollingStatus(status: ScrapingBlueprintStatus) {
  return status === "queued" || status === "running" || status === "generating";
}

export function isBlueprintTerminalStatus(status: ScrapingBlueprintStatus) {
  return TERMINAL_STATUSES.has(status);
}

/** True when an older/in-flight poll must not overwrite fresher local state. */
export function isStaleBlueprintPoll(
  current: ScrapingBlueprint | undefined,
  incoming: ScrapingBlueprint,
): boolean {
  if (!current || current.id !== incoming.id) return false;
  // Never regress from a settled status back to an active generation status.
  if (isBlueprintTerminalStatus(current.status) && isBlueprintPollingStatus(incoming.status)) {
    return true;
  }
  const currentTs = Date.parse(current.updated_at);
  const incomingTs = Date.parse(incoming.updated_at);
  if (Number.isFinite(currentTs) && Number.isFinite(incomingTs) && incomingTs < currentTs) {
    return true;
  }
  return false;
}

export function applyBlueprintPollUpdate(
  current: ScrapingBlueprint[],
  incoming: ScrapingBlueprint,
): ScrapingBlueprint[] {
  const existing = current.find((blueprint) => blueprint.id === incoming.id);
  if (isStaleBlueprintPoll(existing, incoming)) {
    return current;
  }
  if (!existing) {
    return [incoming, ...current];
  }
  return current.map((blueprint) => (blueprint.id === incoming.id ? incoming : blueprint));
}

/**
 * Merge a fresh list response with an optional status poll for one blueprint.
 * Protects against out-of-order responses using updated_at / terminal guards.
 */
export function mergeBlueprintPollState(
  current: ScrapingBlueprint[],
  listed: ScrapingBlueprint[],
  polled?: ScrapingBlueprint | null,
): ScrapingBlueprint[] {
  let next = listed.map((item) => {
    const previous = current.find((blueprint) => blueprint.id === item.id);
    return isStaleBlueprintPoll(previous, item) && previous ? previous : item;
  });

  // Keep any local rows the list omitted only when they are still polling (rare race).
  for (const item of current) {
    if (
      !next.some((blueprint) => blueprint.id === item.id) &&
      isBlueprintPollingStatus(item.status)
    ) {
      next = [...next, item];
    }
  }

  if (polled) {
    next = applyBlueprintPollUpdate(next, polled);
  }

  return next.sort((a, b) => b.version - a.version);
}

export function resolveFollowedBlueprintSelection(
  blueprints: ScrapingBlueprint[],
  currentSelectedId: string,
  followBlueprintId: string | null,
): string {
  if (followBlueprintId && blueprints.some((blueprint) => blueprint.id === followBlueprintId)) {
    return followBlueprintId;
  }
  if (currentSelectedId && blueprints.some((blueprint) => blueprint.id === currentSelectedId)) {
    return currentSelectedId;
  }
  return blueprints[0]?.id ?? "";
}

export async function pollBlueprintUntilSettled(
  fetchStatus: (signal: AbortSignal) => Promise<ScrapingBlueprint>,
  onUpdate: (blueprint: ScrapingBlueprint) => void,
  options: { intervalMs?: number; signal?: AbortSignal } = {},
): Promise<ScrapingBlueprint | null> {
  const intervalMs = options.intervalMs ?? 2_000;
  const controller = new AbortController();
  const abort = () => controller.abort();
  options.signal?.addEventListener("abort", abort, { once: true });

  let latest: ScrapingBlueprint | null = null;
  let requestSequence = 0;

  try {
    while (!controller.signal.aborted) {
      const sequence = ++requestSequence;
      const blueprint = await fetchStatus(controller.signal);
      latest = blueprint;
      if (controller.signal.aborted || sequence !== requestSequence) {
        return latest;
      }
      onUpdate(blueprint);
      if (isBlueprintTerminalStatus(blueprint.status)) return blueprint;
      await wait(intervalMs, controller.signal);
    }
    return latest;
  } catch (error) {
    if (
      isAbortError(error) ||
      isRequestCancelled(error) ||
      controller.signal.aborted ||
      options.signal?.aborted
    ) {
      return latest;
    }
    throw error;
  } finally {
    options.signal?.removeEventListener("abort", abort);
  }
}

function wait(durationMs: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("Request was cancelled.", "AbortError"));
      return;
    }
    const timer = setTimeout(resolve, durationMs);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        reject(new DOMException("Request was cancelled.", "AbortError"));
      },
      { once: true },
    );
  });
}
