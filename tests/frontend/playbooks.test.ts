import assert from "node:assert/strict";
import test from "node:test";

import type {
  ApiPlaybook,
  ApiPlaybookObservation,
  ApiPlaybookRun,
} from "../../src/lib/api/types.ts";
import {
  PLAYBOOK_EMPTY_SUMMARY,
  PLAYBOOK_GENERATE_PATH,
  PLAYBOOK_LATEST_RUN_PATH,
  PLAYBOOK_MINE_PATH,
  PLAYBOOK_PENDING_PATH,
  PLAYBOOK_RERUN_PATH,
  PLAYBOOK_PAGE_SUBTITLE,
  PLAYBOOK_POLL_INTERVAL_MS,
  PLAYBOOK_RERUN_NOTE,
  PLAYBOOK_SAVED_COPY,
  buildPlaybookObservationsPath,
  copyClaimsPromptInjection,
  derivePlaybookPageState,
  groupObservationsByCategory,
  isPlaybookFinalizing,
  observationCategoryLabel,
  observationConfidenceLabel,
  observationStatusLabel,
  playbookCoreSummaryText,
  playbookPrimaryAction,
  playbookProgressPercent,
  playbookRunPath,
  playbookShowsGenerateButton,
  playbookShowsRetryButton,
  playbookShowsRerunButton,
  playbookWarningCopy,
  playbookRunStatusLabel,
  shouldApplyPolledRun,
  shouldFetchPlaybookObservations,
  shouldRefetchPlaybookAfterRun,
  shouldResumePlaybookPolling,
  shouldStopPollingForStatus,
} from "../../src/lib/playbooks.ts";

function playbook(overrides: Partial<ApiPlaybook> = {}): ApiPlaybook {
  return {
    id: "pb-1",
    org_id: "org-1",
    user_id: "user-1",
    status: "not_generated",
    injection_enabled: true,
    core_summary: null,
    extraction_version: 1,
    playbook_version: 0,
    last_success_run_id: null,
    last_success_at: null,
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
    ...overrides,
  };
}

function run(overrides: Partial<ApiPlaybookRun> = {}): ApiPlaybookRun {
  return {
    id: "run-1",
    playbook_id: "pb-1",
    kind: "full",
    status: "queued",
    processed_count: 0,
    total_count: 4,
    warning_count: 0,
    error_message: null,
    started_at: null,
    finished_at: null,
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
    ...overrides,
  };
}

function observation(overrides: Partial<ApiPlaybookObservation> = {}): ApiPlaybookObservation {
  return {
    id: "obs-1",
    playbook_id: "pb-1",
    category: "decision",
    subject: "Database",
    observation: "PostgreSQL is the selected database.",
    status: "confirmed",
    confidence: 0.9,
    evidence_count: 2,
    first_observed_at: "2026-07-01T00:00:00Z",
    last_confirmed_at: "2026-08-01T00:00:00Z",
    superseded_by_id: null,
    user_corrected: false,
    user_excluded: false,
    sources: [],
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
    ...overrides,
  };
}

test("empty Playbook is not_generated and shows Generate, not Retry or Rerun", () => {
  const state = derivePlaybookPageState({
    loading: false,
    loadError: null,
    playbook: playbook(),
    run: null,
  });
  assert.equal(state, "not_generated");
  assert.equal(playbookShowsGenerateButton(state), true);
  assert.equal(playbookPrimaryAction(state), "generate");
  assert.equal(playbookShowsRetryButton(state), false);
  assert.equal(playbookShowsRerunButton(), false);
});

test("incremental paths and pending rerun affordance are explicit", () => {
  assert.equal(PLAYBOOK_PENDING_PATH, "/playbooks/me/pending");
  assert.equal(PLAYBOOK_RERUN_PATH, "/playbooks/me/rerun");
  assert.equal(playbookShowsRerunButton(3), true);
  assert.equal(playbookShowsRerunButton(0), false);
});

test("Generate uses the first-generation endpoint once and no request body path extras", () => {
  assert.equal(PLAYBOOK_GENERATE_PATH, "/playbooks/me/generate");
  assert.equal(PLAYBOOK_MINE_PATH, "/playbooks/me");
  assert.equal(PLAYBOOK_LATEST_RUN_PATH, "/playbooks/me/runs/latest");
  assert.equal(playbookRunPath("run-2"), "/playbooks/me/runs/run-2");
});

test("queued run starts polling and does not show Generate", () => {
  const queued = run({ status: "queued" });
  const state = derivePlaybookPageState({
    loading: false,
    loadError: null,
    playbook: playbook(),
    run: queued,
  });
  assert.equal(state, "queued");
  assert.equal(shouldResumePlaybookPolling(queued), true);
  assert.equal(playbookShowsGenerateButton(state), false);
  assert.equal(PLAYBOOK_POLL_INTERVAL_MS, 2000);
});

test("processing state uses counts and clamps progress", () => {
  const processing = run({ status: "processing", processed_count: 2, total_count: 4 });
  const state = derivePlaybookPageState({
    loading: false,
    loadError: null,
    playbook: playbook(),
    run: processing,
  });
  assert.equal(state, "processing");
  assert.equal(playbookProgressPercent(2, 4), 50);
  assert.equal(playbookProgressPercent(9, 4), 100);
  assert.equal(playbookProgressPercent(1, 0), 0);
  assert.equal(isPlaybookFinalizing(processing), false);
});

test("processed_count == total_count while processing is finalizing, not complete", () => {
  const processing = run({ status: "processing", processed_count: 4, total_count: 4 });
  assert.equal(
    derivePlaybookPageState({
      loading: false,
      loadError: null,
      playbook: playbook(),
      run: processing,
    }),
    "processing",
  );
  assert.equal(isPlaybookFinalizing(processing), true);
  assert.equal(shouldRefetchPlaybookAfterRun(processing), false);
});

test("completed run stops polling and refetches Playbook plus observations", () => {
  const completed = run({ status: "completed", processed_count: 4, total_count: 4 });
  assert.equal(shouldResumePlaybookPolling(completed), false);
  assert.equal(shouldRefetchPlaybookAfterRun(completed), true);
  const active = playbook({
    status: "active",
    playbook_version: 1,
    last_success_run_id: completed.id,
    core_summary: "Working style: concise.",
  });
  assert.equal(shouldFetchPlaybookObservations(active), true);
  assert.equal(
    derivePlaybookPageState({
      loading: false,
      loadError: null,
      playbook: active,
      run: completed,
    }),
    "active",
  );
  assert.equal(playbookShowsGenerateButton("active"), false);
});

test("completed with warnings remains a successful display", () => {
  const warned = run({
    status: "completed_with_warnings",
    warning_count: 3,
    processed_count: 3,
    total_count: 4,
  });
  const active = playbook({
    status: "active",
    playbook_version: 1,
    last_success_run_id: warned.id,
  });
  assert.equal(shouldRefetchPlaybookAfterRun(warned), true);
  assert.equal(
    derivePlaybookPageState({
      loading: false,
      loadError: null,
      playbook: active,
      run: warned,
    }),
    "active_with_warnings",
  );
  assert.equal(playbookWarningCopy(warned), "3 source-processing warnings were recorded");
  assert.equal(playbookRunStatusLabel(warned.status), "Completed");
  assert.equal(playbookRunStatusLabel("completed"), "Completed");
  assert.equal(playbookRunStatusLabel(warned.status).includes("with warnings"), false);
  assert.equal(playbookShowsGenerateButton("active_with_warnings"), false);
});

test("failed first generation shows retry against a new run id", () => {
  const failed = run({
    status: "failed",
    error_message: "No eligible Playbook sources could be processed.",
  });
  const state = derivePlaybookPageState({
    loading: false,
    loadError: null,
    playbook: playbook(),
    run: failed,
  });
  assert.equal(state, "failed_without_playbook");
  assert.equal(playbookShowsRetryButton(state), true);
  assert.equal(playbookPrimaryAction(state), "retry");
  const retry = run({ id: "run-2", status: "queued" });
  assert.equal(shouldApplyPolledRun(failed.id, retry), false);
  assert.equal(shouldApplyPolledRun(retry.id, retry), true);
  assert.equal(shouldResumePlaybookPolling(retry), true);
});

test("refresh with an existing processing run resumes polling", () => {
  const processing = run({ status: "processing", processed_count: 1, total_count: 5 });
  assert.equal(shouldResumePlaybookPolling(processing), true);
  assert.equal(
    derivePlaybookPageState({
      loading: false,
      loadError: null,
      playbook: playbook(),
      run: processing,
    }),
    "processing",
  );
});

test("active Playbook displays core summary fallback and hides Generate", () => {
  const active = playbook({
    status: "active",
    playbook_version: 2,
    last_success_run_id: "run-9",
    core_summary: "  Working style: concise.  ",
  });
  assert.equal(playbookCoreSummaryText(active.core_summary), "Working style: concise.");
  assert.equal(playbookCoreSummaryText("   "), PLAYBOOK_EMPTY_SUMMARY);
  assert.equal(playbookShowsGenerateButton("active"), false);
  assert.equal(playbookShowsRerunButton(), false);
});

test("observations are grouped by known category order with unknowns last", () => {
  const grouped = groupObservationsByCategory([
    observation({ id: "1", category: "blocker", subject: "Auth" }),
    observation({ id: "2", category: "decision", subject: "DB" }),
    observation({ id: "3", category: "custom_note", subject: "Other" }),
    observation({ id: "4", category: "preference", subject: "Style" }),
  ]);
  assert.deepEqual(
    grouped.map((group) => group.category),
    ["preference", "decision", "blocker", "custom_note"],
  );
  assert.equal(observationCategoryLabel("completed_work"), "Completed Work");
  assert.equal(observationCategoryLabel("custom_note"), "Custom Note");
});

test("excluded observations are not requested on the normal page path", () => {
  assert.equal(buildPlaybookObservationsPath(), "/playbooks/me/observations");
  assert.equal(buildPlaybookObservationsPath({}), "/playbooks/me/observations");
  assert.ok(!buildPlaybookObservationsPath().includes("include_excluded"));
  assert.equal(
    buildPlaybookObservationsPath({ include_excluded: true }),
    "/playbooks/me/observations?include_excluded=true",
  );
});

test("stale run responses are ignored and poll cleanup statuses stop the loop", () => {
  const current = run({ id: "run-new", status: "processing" });
  const stale = run({ id: "run-old", status: "failed" });
  assert.equal(shouldApplyPolledRun(current.id, stale), false);
  assert.equal(shouldApplyPolledRun(current.id, current), true);
  assert.equal(shouldApplyPolledRun(null, current), false);
  assert.equal(shouldStopPollingForStatus(401), true);
  assert.equal(shouldStopPollingForStatus(403), true);
  assert.equal(shouldStopPollingForStatus(404), true);
  assert.equal(shouldStopPollingForStatus(500), false);
  assert.equal(shouldStopPollingForStatus(0), false);
});

test("API errors and observation labels render safely", () => {
  assert.equal(observationStatusLabel("confirmed"), "Confirmed");
  assert.equal(observationStatusLabel("rejected"), "Rejected");
  assert.equal(observationConfidenceLabel(0.8), "High confidence");
  assert.equal(observationConfidenceLabel(0.79), "Medium confidence");
  assert.equal(observationConfidenceLabel(0.49), "Low confidence");
  assert.equal(observationConfidenceLabel(null), null);
  const failed = run({ status: "failed", error_message: "Playbook generation failed." });
  assert.equal(failed.error_message?.includes("redis://"), false);
  assert.equal(
    derivePlaybookPageState({
      loading: false,
      loadError: "Could not load your Playbook.",
      playbook: null,
      run: null,
    }),
    "load_error",
  );
});

test("Phase 5 copy does not claim prompt injection, rerun, or pending counts", () => {
  const copy = [PLAYBOOK_PAGE_SUBTITLE, PLAYBOOK_SAVED_COPY, PLAYBOOK_RERUN_NOTE].join("\n");
  assert.equal(copyClaimsPromptInjection(copy), false);
  assert.equal(copy.toLowerCase().includes("rerun playbooks"), false);
  assert.equal(copy.toLowerCase().includes("pending"), false);
  assert.equal(copyClaimsPromptInjection("Your Playbook is now active in every chat."), true);
});

test("active Playbook keeps displaying after a later failed run", () => {
  const state = derivePlaybookPageState({
    loading: false,
    loadError: null,
    playbook: playbook({ status: "active", playbook_version: 1, last_success_run_id: "run-1" }),
    run: run({ id: "run-9", status: "failed", error_message: "Playbook persistence failed." }),
  });
  assert.equal(state, "active");
  assert.equal(playbookShowsRetryButton(state), false);
  assert.equal(playbookShowsGenerateButton(state), false);
});

test("initial loading does not flash the empty generate state", () => {
  assert.equal(
    derivePlaybookPageState({
      loading: true,
      loadError: null,
      playbook: null,
      run: null,
    }),
    "initial_loading",
  );
  assert.equal(playbookShowsGenerateButton("initial_loading"), false);
});
