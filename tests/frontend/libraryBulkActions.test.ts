import assert from "node:assert/strict";
import test from "node:test";

import {
  addLabelToIds,
  bulkResultMessage,
  intersectSelection,
  mapWithConcurrencyLimit,
  removeLabelFromIds,
  selectAll,
  selectRange,
  summarizeBulkResults,
  toggleSelection,
} from "../../src/lib/libraryBulkActions.ts";

test("mapWithConcurrencyLimit never runs more than the given number of tasks at once", async () => {
  const items = Array.from({ length: 10 }, (_, i) => i);
  let active = 0;
  let maxActive = 0;
  const results = await mapWithConcurrencyLimit(items, 3, async (item) => {
    active += 1;
    maxActive = Math.max(maxActive, active);
    await new Promise((resolve) => setTimeout(resolve, 5));
    active -= 1;
    return item * 2;
  });
  assert.ok(maxActive <= 3, `expected at most 3 concurrent tasks, saw ${maxActive}`);
  assert.equal(maxActive, 3, "10 items over a limit of 3 should actually reach the cap");
  assert.deepEqual(
    results.map((r) => (r.status === "fulfilled" ? r.value : null)),
    items.map((i) => i * 2),
  );
});

test("mapWithConcurrencyLimit reports per-item failures without aborting the rest", async () => {
  const items = ["a", "b", "c", "d"];
  const results = await mapWithConcurrencyLimit(items, 2, async (item) => {
    if (item === "b") throw new Error("boom");
    return `ok:${item}`;
  });
  assert.equal(results.length, 4);
  assert.deepEqual(
    results.map((r) => r.status),
    ["fulfilled", "rejected", "fulfilled", "fulfilled"],
  );
  assert.equal((results[0] as PromiseFulfilledResult<string>).value, "ok:a");
  assert.equal(((results[1] as PromiseRejectedResult).reason as Error).message, "boom");
  assert.equal((results[2] as PromiseFulfilledResult<string>).value, "ok:c");
  assert.equal((results[3] as PromiseFulfilledResult<string>).value, "ok:d");
});

test("mapWithConcurrencyLimit handles an empty list and a limit larger than the list", async () => {
  assert.deepEqual(await mapWithConcurrencyLimit([], 6, async () => "x"), []);
  const results = await mapWithConcurrencyLimit(["only"], 50, async (item) => item);
  assert.equal(results.length, 1);
  assert.equal((results[0] as PromiseFulfilledResult<string>).value, "only");
});

test("toggleSelection adds then removes an id without disturbing others", () => {
  const empty = new Set<string>();
  const withA = toggleSelection(empty, "a");
  assert.deepEqual([...withA], ["a"]);
  const withAB = toggleSelection(withA, "b");
  assert.deepEqual([...withAB].sort(), ["a", "b"]);
  const withBOnly = toggleSelection(withAB, "a");
  assert.deepEqual([...withBOnly], ["b"]);
});

test("selectRange selects an inclusive range regardless of direction", () => {
  const ids = ["a", "b", "c", "d", "e"];
  const forward = selectRange(new Set(), ids, 1, 3);
  assert.deepEqual([...forward].sort(), ["b", "c", "d"]);
  const backward = selectRange(new Set(), ids, 3, 1);
  assert.deepEqual([...backward].sort(), ["b", "c", "d"]);
});

test("selectRange keeps prior selections outside the new range", () => {
  const ids = ["a", "b", "c", "d"];
  const prior = new Set(["a"]);
  const next = selectRange(prior, ids, 2, 3);
  assert.deepEqual([...next].sort(), ["a", "c", "d"]);
});

test("selectRange clamps out-of-bounds indices", () => {
  const ids = ["a", "b", "c"];
  const next = selectRange(new Set(), ids, -5, 50);
  assert.deepEqual([...next].sort(), ["a", "b", "c"]);
});

test("selectAll replaces the selection with exactly the visible ids", () => {
  assert.deepEqual([...selectAll(["x", "y"])].sort(), ["x", "y"]);
  assert.deepEqual([...selectAll([])], []);
});

test("intersectSelection drops ids no longer visible after a folder/search/sort change", () => {
  const selected = new Set(["a", "b", "c"]);
  const afterFilterChange = intersectSelection(selected, ["b", "d"]);
  assert.deepEqual([...afterFilterChange], ["b"]);
  assert.deepEqual([...intersectSelection(selected, [])], []);
});

test("addLabelToIds is idempotent and preserves existing order", () => {
  assert.deepEqual(addLabelToIds(["l1"], "l2"), ["l1", "l2"]);
  assert.deepEqual(addLabelToIds(["l1", "l2"], "l2"), ["l1", "l2"]);
});

test("removeLabelFromIds drops only the targeted label", () => {
  assert.deepEqual(removeLabelFromIds(["l1", "l2", "l3"], "l2"), ["l1", "l3"]);
  assert.deepEqual(removeLabelFromIds(["l1"], "l9"), ["l1"]);
});

test("summarizeBulkResults separates successes from failures and preserves ids", () => {
  const outcome = summarizeBulkResults([
    { id: "a", ok: true },
    { id: "b", ok: false },
    { id: "c", ok: true },
  ]);
  assert.deepEqual(outcome.successIds, ["a", "c"]);
  assert.deepEqual(outcome.failedIds, ["b"]);
  assert.equal(outcome.succeeded, 2);
  assert.equal(outcome.failed, 1);
});

test("bulkResultMessage never claims success when something failed", () => {
  const allOk = summarizeBulkResults([
    { id: "a", ok: true },
    { id: "b", ok: true },
  ]);
  assert.deepEqual(bulkResultMessage("Moved", allOk), { level: "success", text: "Moved 2 items" });

  const partial = summarizeBulkResults([
    { id: "a", ok: true },
    { id: "b", ok: false },
  ]);
  assert.deepEqual(bulkResultMessage("Moved", partial), {
    level: "error",
    text: "Moved 1 of 2 items — 1 failed",
  });

  const allFailed = summarizeBulkResults([
    { id: "a", ok: false },
    { id: "b", ok: false },
  ]);
  assert.deepEqual(bulkResultMessage("Moved", allFailed), {
    level: "error",
    text: "Moved failed for all 2 items",
  });
});

test("bulkResultMessage uses singular wording for a single item", () => {
  const single = summarizeBulkResults([{ id: "a", ok: true }]);
  assert.deepEqual(bulkResultMessage("Deleted", single), {
    level: "success",
    text: "Deleted 1 item",
  });
});
