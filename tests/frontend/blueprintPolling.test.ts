import assert from "node:assert/strict";
import test from "node:test";
import {
  isBlueprintPollingStatus,
  pollBlueprintUntilSettled,
} from "../../src/lib/scraping/blueprintPolling";
import type { ScrapingBlueprint } from "../../src/lib/scraping/types";

function blueprint(status: ScrapingBlueprint["status"]): ScrapingBlueprint {
  return {
    id: "blueprint-1",
    mission_id: "mission-1",
    version: 1,
    display_name: null,
    status,
    model_set_id: "set-1",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

test("blueprint polling tracks only active generation statuses", () => {
  assert.equal(isBlueprintPollingStatus("queued"), true);
  assert.equal(isBlueprintPollingStatus("running"), true);
  assert.equal(isBlueprintPollingStatus("ready_for_review"), false);
  assert.equal(isBlueprintPollingStatus("failed"), false);
});

test("blueprint polling stops after ready for review", async () => {
  const results = [blueprint("queued"), blueprint("running"), blueprint("ready_for_review")];
  const updates: string[] = [];
  let calls = 0;

  const result = await pollBlueprintUntilSettled(
    async () => results[calls++],
    (value) => updates.push(value.status),
    { intervalMs: 0 },
  );

  assert.equal(calls, 3);
  assert.equal(result?.status, "ready_for_review");
  assert.deepEqual(updates, ["queued", "running", "ready_for_review"]);
});

test("blueprint polling stops cleanly when navigation aborts it", async () => {
  const controller = new AbortController();
  let calls = 0;
  const result = await pollBlueprintUntilSettled(
    async () => {
      calls += 1;
      controller.abort();
      return blueprint("queued");
    },
    () => undefined,
    { intervalMs: 0, signal: controller.signal },
  );

  assert.equal(result, null);
  assert.equal(calls, 1);
});
