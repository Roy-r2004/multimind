import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";
import {
  campaignStatusLabel,
  isCampaignPollingStatus,
  latestCampaignSequence,
  mergeCampaignEvents,
} from "../../src/lib/scraping/campaignCockpit";
import type { ScrapingEvent } from "../../src/lib/scraping/types";

function event(sequence_number: number): ScrapingEvent {
  return {
    id: `event-${sequence_number}`,
    execution_id: "execution-1",
    sequence_number,
    event_type: "progress",
    message: `Event ${sequence_number}`,
    metadata_json: {},
    created_at: "2026-01-01T00:00:00Z",
  };
}

test("campaign polling covers active lifecycle states only", () => {
  assert.equal(isCampaignPollingStatus("queued"), true);
  assert.equal(isCampaignPollingStatus("pause_requested"), true);
  assert.equal(isCampaignPollingStatus("paused"), false);
  assert.equal(isCampaignPollingStatus("completed"), false);
});

test("campaign event merging is ordered and deduplicated by sequence", () => {
  const merged = mergeCampaignEvents([event(1), event(3)], [event(2), event(3)]);

  assert.deepEqual(
    merged.map((item) => item.sequence_number),
    [1, 2, 3],
  );
  assert.equal(latestCampaignSequence(merged), 3);
});

test("campaign status labels are readable", () => {
  assert.equal(campaignStatusLabel("pause_requested"), "pause requested");
});

test("campaign cockpit hides pricing and uses simple test-campaign wording", () => {
  const source = readFileSync(
    resolve(import.meta.dirname, "../../src/routes/scraping.$missionId.campaigns.$executionId.tsx"),
    "utf8",
  );
  assert.doesNotMatch(source, /Budget used/);
  assert.doesNotMatch(source, /Campaign budget/);
  assert.doesNotMatch(source, /formatMoney/);
  assert.doesNotMatch(source, /\$\d/);
  assert.doesNotMatch(source, /deterministic mock execution/);
  assert.match(source, /Test campaign/);
  assert.match(source, /lg:grid-cols-3/);
});
