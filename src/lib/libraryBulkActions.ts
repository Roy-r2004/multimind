/** Pure selection and bulk-action helpers for the Library browse view. */

/** Cap on simultaneous in-flight requests for a single bulk action. */
export const LIBRARY_BULK_CONCURRENCY_LIMIT = 6;

/**
 * Run `fn` over `items` with at most `limit` requests in flight at once.
 * Never throws: each item's outcome is reported as a settled result, same
 * shape as Promise.allSettled, so one failure can't abort the rest and a
 * large selection can't fire hundreds of simultaneous requests.
 */
export async function mapWithConcurrencyLimit<T, R>(
  items: T[],
  limit: number,
  fn: (item: T, index: number) => Promise<R>,
): Promise<PromiseSettledResult<R>[]> {
  const results: PromiseSettledResult<R>[] = new Array(items.length);
  let nextIndex = 0;

  async function worker(): Promise<void> {
    for (;;) {
      const index = nextIndex;
      nextIndex += 1;
      if (index >= items.length) return;
      try {
        const value = await fn(items[index] as T, index);
        results[index] = { status: "fulfilled", value };
      } catch (reason) {
        results[index] = { status: "rejected", reason };
      }
    }
  }

  const workerCount = Math.max(1, Math.min(limit, items.length));
  await Promise.all(Array.from({ length: workerCount }, () => worker()));
  return results;
}

export function toggleSelection(selected: ReadonlySet<string>, id: string): Set<string> {
  const next = new Set(selected);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  return next;
}

/** Shift-click range select between the last touched row and the current one. */
export function selectRange(
  selected: ReadonlySet<string>,
  visibleIds: string[],
  fromIndex: number,
  toIndex: number,
): Set<string> {
  const next = new Set(selected);
  const start = Math.max(0, Math.min(fromIndex, toIndex));
  const end = Math.min(visibleIds.length - 1, Math.max(fromIndex, toIndex));
  for (let i = start; i <= end; i += 1) {
    const id = visibleIds[i];
    if (id) next.add(id);
  }
  return next;
}

export function selectAll(visibleIds: string[]): Set<string> {
  return new Set(visibleIds);
}

/** Drop any selected id that is no longer in the current (filtered/sorted) list. */
export function intersectSelection(
  selected: ReadonlySet<string>,
  visibleIds: string[],
): Set<string> {
  const valid = new Set(visibleIds);
  const next = new Set<string>();
  for (const id of selected) {
    if (valid.has(id)) next.add(id);
  }
  return next;
}

export function addLabelToIds(existing: string[], labelId: string): string[] {
  return existing.includes(labelId) ? existing : [...existing, labelId];
}

export function removeLabelFromIds(existing: string[], labelId: string): string[] {
  return existing.filter((id) => id !== labelId);
}

export type LibraryBulkOutcome = {
  successIds: string[];
  failedIds: string[];
  succeeded: number;
  failed: number;
};

export function summarizeBulkResults(results: { id: string; ok: boolean }[]): LibraryBulkOutcome {
  const successIds = results.filter((r) => r.ok).map((r) => r.id);
  const failedIds = results.filter((r) => !r.ok).map((r) => r.id);
  return { successIds, failedIds, succeeded: successIds.length, failed: failedIds.length };
}

/** Never claim full success when any item failed — report the real counts. */
export function bulkResultMessage(
  action: string,
  outcome: LibraryBulkOutcome,
): { level: "success" | "error"; text: string } {
  const total = outcome.succeeded + outcome.failed;
  const plural = total === 1 ? "item" : "items";
  if (outcome.failed === 0) {
    return { level: "success", text: `${action} ${outcome.succeeded} ${plural}` };
  }
  if (outcome.succeeded === 0) {
    return { level: "error", text: `${action} failed for all ${total} ${plural}` };
  }
  return {
    level: "error",
    text: `${action} ${outcome.succeeded} of ${total} ${plural} — ${outcome.failed} failed`,
  };
}
