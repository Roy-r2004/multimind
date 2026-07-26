import assert from "node:assert/strict";
import test from "node:test";
import {
  applyBlueprintPollUpdate,
  isBlueprintPollingStatus,
  isBlueprintTerminalStatus,
  isStaleBlueprintPoll,
  mergeBlueprintPollState,
  pollBlueprintUntilSettled,
  resolveFollowedBlueprintSelection,
} from "../../src/lib/scraping/blueprintPolling";
import type { ScrapingBlueprint } from "../../src/lib/scraping/types";

function blueprint(
  status: ScrapingBlueprint["status"],
  overrides: Partial<ScrapingBlueprint> = {},
): ScrapingBlueprint {
  return {
    id: "blueprint-1",
    mission_id: "mission-1",
    version: 1,
    display_name: null,
    status,
    model_set_id: "set-1",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

test("blueprint polling tracks only active generation statuses", () => {
  assert.equal(isBlueprintPollingStatus("queued"), true);
  assert.equal(isBlueprintPollingStatus("running"), true);
  assert.equal(isBlueprintPollingStatus("ready_for_review"), false);
  assert.equal(isBlueprintPollingStatus("failed"), false);
  assert.equal(isBlueprintTerminalStatus("ready_for_review"), true);
  assert.equal(isBlueprintTerminalStatus("approved"), true);
  assert.equal(isBlueprintTerminalStatus("running"), false);
});

test("running backend response later ready_for_review updates without remount", async () => {
  const results = [
    blueprint("running", { updated_at: "2026-01-01T00:00:01Z" }),
    blueprint("ready_for_review", {
      updated_at: "2026-01-01T00:00:02Z",
      human_readable_blueprint: "Completed research",
      structured_blueprint: { country_dossier: { country_name: "Austria" } },
    }),
  ];
  let state = [blueprint("running", { updated_at: "2026-01-01T00:00:00Z" })];
  let calls = 0;

  const result = await pollBlueprintUntilSettled(
    async () => results[calls++],
    (value) => {
      state = applyBlueprintPollUpdate(state, value);
    },
    { intervalMs: 0 },
  );

  assert.equal(calls, 2);
  assert.equal(result?.status, "ready_for_review");
  assert.equal(state[0]?.status, "ready_for_review");
  assert.equal(state[0]?.human_readable_blueprint, "Completed research");
  assert.equal(isBlueprintPollingStatus(state[0]!.status), false);
});

test("polling stops after ready_for_review and does not keep requesting", async () => {
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

test("stale running poll cannot restore running after ready_for_review", () => {
  const ready = blueprint("ready_for_review", { updated_at: "2026-01-01T00:00:05Z" });
  const staleRunning = blueprint("running", { updated_at: "2026-01-01T00:00:01Z" });

  assert.equal(isStaleBlueprintPoll(ready, staleRunning), true);
  const next = applyBlueprintPollUpdate([ready], staleRunning);
  assert.equal(next[0]?.status, "ready_for_review");
});

test("version history merge updates status and preserves active ordering", () => {
  const current = [
    blueprint("running", { id: "b2", version: 2, updated_at: "2026-01-01T00:00:01Z" }),
    blueprint("approved", { id: "b1", version: 1, updated_at: "2026-01-01T00:00:00Z" }),
  ];
  const listed = [
    blueprint("ready_for_review", {
      id: "b2",
      version: 2,
      updated_at: "2026-01-01T00:00:03Z",
      structured_blueprint: { regions: ["Vienna"] },
    }),
    blueprint("approved", { id: "b1", version: 1, updated_at: "2026-01-01T00:00:00Z" }),
  ];

  const merged = mergeBlueprintPollState(current, listed);
  assert.equal(merged[0]?.id, "b2");
  assert.equal(merged[0]?.status, "ready_for_review");
  assert.deepEqual(merged[0]?.structured_blueprint, { regions: ["Vienna"] });
  assert.equal(merged[1]?.status, "approved");
});

test("followed generation is auto-selected while historical selection stays when not following", () => {
  const versions = [
    blueprint("queued", { id: "new", version: 3 }),
    blueprint("ready_for_review", { id: "old", version: 2 }),
  ];
  assert.equal(resolveFollowedBlueprintSelection(versions, "old", "new"), "new");
  assert.equal(resolveFollowedBlueprintSelection(versions, "old", null), "old");
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

  assert.equal(result?.status ?? null, "queued");
  assert.equal(calls, 1);
});
