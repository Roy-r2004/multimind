import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";
import {
  applyCampaignControlSummary,
  campaignActionFlags,
  campaignStatusLabel,
  clarificationNeedsReviewMessage,
  clarificationStatusLabel,
  isCampaignPollingStatus,
  latestCampaignSequence,
  mergeCampaignEvents,
} from "../../src/lib/scraping/campaignCockpit";
import type {
  ScrapingEvent,
  ScrapingExecutionDetail,
  ScrapingExecutionSummary,
} from "../../src/lib/scraping/types";

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

function baseDetail(status: ScrapingExecutionSummary["status"]): ScrapingExecutionDetail {
  return {
    execution: {
      id: "execution-1",
      organization_id: "org-1",
      mission_id: "mission-1",
      blueprint_id: "blueprint-1",
      execution_type: "mission_campaign",
      mode: "mock",
      status,
      status_label: status,
      country_code: "AT",
      country_name: "Austria",
      clarification_status: "not_required",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      sources_discovered: 0,
      documents_found: 0,
      records_extracted: 0,
      records_verified: 0,
      duplicates_detected: 0,
      blocked_sources: 0,
      coverage_debt: 0,
    },
    agents: [],
    task_summary_counts: {},
    coverage_summary_counts: {},
    recent_tasks: [],
    recent_events: [],
    can_cancel: false,
    can_pause: false,
    can_resume: false,
    can_delete: false,
    mock: true,
  };
}

function summary(
  status: ScrapingExecutionSummary["status"],
  overrides: Partial<ScrapingExecutionSummary> = {},
): ScrapingExecutionSummary {
  return {
    ...baseDetail(status).execution,
    ...overrides,
    status,
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

test("clarification status labels cover safe cockpit states", () => {
  assert.equal(clarificationStatusLabel("not_required"), "Clarification not required");
  assert.equal(clarificationStatusLabel("completed"), "Clarification completed");
  assert.equal(clarificationStatusLabel("in_progress"), "Clarification in progress");
  assert.equal(clarificationStatusLabel("requires_human_review"), "Clarification needs review");
  assert.equal(clarificationStatusLabel("failed"), "Clarification failed");
  assert.match(
    clarificationNeedsReviewMessage("requires_human_review") ?? "",
    /cannot continue automatically/,
  );
});

test("paused status shows Resume and Cancel, not Pause", () => {
  const flags = campaignActionFlags("paused", "not_required");
  assert.equal(flags.canResume, true);
  assert.equal(flags.canCancel, true);
  assert.equal(flags.canPause, false);
});

test("running status shows Pause and Cancel, not Resume", () => {
  const flags = campaignActionFlags("running", "not_required");
  assert.equal(flags.canPause, true);
  assert.equal(flags.canCancel, true);
  assert.equal(flags.canResume, false);
});

test("cancelled status does not show Resume", () => {
  const flags = campaignActionFlags("cancelled", "not_required");
  assert.equal(flags.canResume, false);
  assert.equal(flags.canPause, false);
  assert.equal(flags.canCancel, false);
});

test("human-review paused campaigns do not show operable Resume", () => {
  const flags = campaignActionFlags("paused", "requires_human_review");
  assert.equal(flags.canResume, false);
  assert.equal(flags.canCancel, true);
});

test("Resume mutation applies returned summary immediately", () => {
  const before = baseDetail("paused");
  before.can_resume = true;
  before.can_cancel = true;
  const after = applyCampaignControlSummary(
    before,
    summary("queued", { resumed_at: "2026-01-01T01:00:00Z" }),
  );
  assert.equal(after.execution.status, "queued");
  assert.equal(after.execution.resumed_at, "2026-01-01T01:00:00Z");
  assert.equal(after.can_resume, false);
  assert.equal(after.can_pause, false);
  assert.equal(after.can_cancel, true);
});

test("pause acknowledgement summary enables Resume when status becomes paused", () => {
  const after = applyCampaignControlSummary(baseDetail("running"), summary("paused"));
  assert.equal(after.execution.status, "paused");
  assert.equal(after.can_resume, true);
  assert.equal(after.can_pause, false);
  assert.equal(after.can_cancel, true);
});

test("campaign cockpit uses status-derived actions and resumeMissionCampaign", () => {
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
  assert.match(source, /clarificationStatusLabel/);
  assert.match(source, /lg:grid-cols-3/);
  assert.match(source, /campaignActionFlags/);
  assert.match(source, /applyCampaignControlSummary/);
  assert.match(source, /resumeMissionCampaign/);
  assert.match(source, /actions\.canResume/);
  assert.match(source, /setActing\(false\)/);
  assert.match(source, /Campaign control failed/);
  assert.match(source, /catch \(err\)/);
});

test("Resume API failure path clears Updating and surfaces an error", () => {
  const source = readFileSync(
    resolve(import.meta.dirname, "../../src/routes/scraping.$missionId.campaigns.$executionId.tsx"),
    "utf8",
  );
  // control() must clear acting in both success and failure paths, never leave Updating stuck.
  const catchBlock = source.slice(source.indexOf("} catch (err) {"));
  assert.match(catchBlock, /setError\(/);
  assert.match(catchBlock, /setActing\(false\)/);
});
