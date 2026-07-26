import { isAbortError } from "@/lib/api/client";
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

export async function pollBlueprintUntilSettled(
  fetchStatus: (signal: AbortSignal) => Promise<ScrapingBlueprint>,
  onUpdate: (blueprint: ScrapingBlueprint) => void,
  options: { intervalMs?: number; signal?: AbortSignal } = {},
): Promise<ScrapingBlueprint | null> {
  const intervalMs = options.intervalMs ?? 2_000;
  const controller = new AbortController();
  const abort = () => controller.abort();
  options.signal?.addEventListener("abort", abort, { once: true });

  try {
    while (!controller.signal.aborted) {
      const blueprint = await fetchStatus(controller.signal);
      onUpdate(blueprint);
      if (TERMINAL_STATUSES.has(blueprint.status)) return blueprint;
      await wait(intervalMs, controller.signal);
    }
    return null;
  } catch (error) {
    if (isAbortError(error) || controller.signal.aborted) return null;
    throw error;
  } finally {
    options.signal?.removeEventListener("abort", abort);
  }
}

function wait(durationMs: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
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
