import assert from "node:assert/strict";
import test from "node:test";

import {
  applyDebateEvent,
  buildChallengeRows,
  canStartNewDebateAttempt,
  challengeRowKey,
  debateEligibility,
  debateUsageDisplay,
  groupRowsByTarget,
  isDebateStatusActive,
  isDebateStatusTerminal,
  isZeroChallengeDebate,
  mergeDebateSnapshot,
  modelDebateOutcome,
  remainingDebateAttempts,
} from "../../src/lib/debateState.ts";

function session(overrides: Record<string, unknown> = {}) {
  return {
    id: "session-1",
    turn_id: "turn-1",
    status: "running",
    model_ids: ["model-a", "model-b", "model-c"],
    agreements: [],
    unchallenged_model_ids: [],
    unresolved_disagreements: [],
    corrections: [],
    risks: [],
    uncertainties: [],
    final_summary: null,
    error_message: null,
    started_at: "2026-01-01T00:00:00Z",
    completed_at: null,
    created_at: "2026-01-01T00:00:00Z",
    challenges: [],
    responses: [],
    usage: { total_tokens_input: 0, total_tokens_output: 0, total_cost_usd: 0, calls_with_unknown_usage: 0 },
    ...overrides,
  };
}

function answer(modelId: string, status = "completed") {
  return {
    model_id: modelId,
    model_name: modelId,
    status,
    tokens_input: 0,
    tokens_output: 0,
  };
}

// --- 1. Debate button eligibility -------------------------------------------

test("eligibility requires a finalized verdict", () => {
  const result = debateEligibility({ hasVerdict: false, completedAnswers: [answer("a"), answer("b")] });
  assert.equal(result.eligible, false);
  assert.match((result as { reason: string }).reason, /Final Verdict/);
});

test("eligibility requires at least two completed answers", () => {
  const result = debateEligibility({ hasVerdict: true, completedAnswers: [answer("a")] });
  assert.equal(result.eligible, false);
  assert.match((result as { reason: string }).reason, /at least two/);
});

test("eligibility passes with a verdict and two completed answers", () => {
  const result = debateEligibility({ hasVerdict: true, completedAnswers: [answer("a"), answer("b")] });
  assert.equal(result.eligible, true);
});

// --- 9. Composite challenge identity -----------------------------------------

test("challenges with the same bare id from different challengers never collide", () => {
  const s = session({
    challenges: [
      {
        id: "a",
        author_model_id: "model-a",
        status: "completed",
        challenges: [{ challenge_id: "c1", target_model_id: "model-b", issue: "x", reasoning: "y", severity: "low" }],
        created_at: "",
      },
      {
        id: "c",
        author_model_id: "model-c",
        status: "completed",
        challenges: [{ challenge_id: "c1", target_model_id: "model-b", issue: "z", reasoning: "w", severity: "medium" }],
        created_at: "",
      },
    ],
    responses: [
      {
        id: "b",
        author_model_id: "model-b",
        status: "completed",
        responses: [
          { challenge_id: "c1", challenger_model_id: "model-a", stance: "defends", explanation: "re a" },
          { challenge_id: "c1", challenger_model_id: "model-c", stance: "concedes", explanation: "re c", updated_position: "fixed" },
        ],
        created_at: "",
      },
    ],
  });
  const rows = buildChallengeRows(s);
  assert.equal(rows.length, 2);
  assert.equal(rows[0].key, challengeRowKey("model-a", "c1"));
  assert.equal(rows[1].key, challengeRowKey("model-c", "c1"));
  assert.notEqual(rows[0].key, rows[1].key);
});

// --- 10. Defends / revises / concedes -----------------------------------------

test("a composite row resolves to the response addressed to its own challenger, not the other one", () => {
  const s = session({
    challenges: [
      {
        id: "a",
        author_model_id: "model-a",
        status: "completed",
        challenges: [{ challenge_id: "c1", target_model_id: "model-b", issue: "x", reasoning: "y", severity: "low" }],
        created_at: "",
      },
      {
        id: "c",
        author_model_id: "model-c",
        status: "completed",
        challenges: [{ challenge_id: "c1", target_model_id: "model-b", issue: "z", reasoning: "w", severity: "medium" }],
        created_at: "",
      },
    ],
    responses: [
      {
        id: "b",
        author_model_id: "model-b",
        status: "completed",
        responses: [
          { challenge_id: "c1", challenger_model_id: "model-a", stance: "defends", explanation: "re a" },
          { challenge_id: "c1", challenger_model_id: "model-c", stance: "concedes", explanation: "re c", updated_position: "fixed" },
        ],
        created_at: "",
      },
    ],
  });
  const byChallenger = Object.fromEntries(buildChallengeRows(s).map((r) => [r.challengerModelId, r]));
  assert.equal(byChallenger["model-a"].response?.stance, "defends");
  assert.equal(byChallenger["model-c"].response?.stance, "concedes");
});

// --- 11. Zero-challenge debates ------------------------------------------------

test("a debate where nobody challenged anybody is reported as zero-challenge", () => {
  const s = session({
    status: "completed",
    challenges: [
      { id: "a", author_model_id: "model-a", status: "completed", challenges: [], created_at: "" },
      { id: "b", author_model_id: "model-b", status: "completed", challenges: [], created_at: "" },
    ],
    unchallenged_model_ids: ["model-a", "model-b"],
  });
  assert.equal(isZeroChallengeDebate(s), true);
  assert.equal(modelDebateOutcome("model-a", s), "unchallenged");
});

// --- 12. Partial failures -------------------------------------------------------

test("a model whose Round-2 call failed outright is reported as failed, not unchallenged", () => {
  const s = session({
    challenges: [{ id: "a", author_model_id: "model-a", status: "failed", challenges: null, created_at: "" }],
  });
  assert.equal(modelDebateOutcome("model-a", s), "failed");
});

test("a model that raised no challenges but wasn't itself challenged is unchallenged, not failed", () => {
  const s = session({
    challenges: [{ id: "a", author_model_id: "model-a", status: "completed", challenges: [], created_at: "" }],
  });
  assert.equal(modelDebateOutcome("model-a", s), "unchallenged");
});

test("debate_challenge_failed applies a per-model failure without touching other models", () => {
  let s = session({
    challenges: [{ id: "b", author_model_id: "model-b", status: "completed", challenges: [], created_at: "" }],
  });
  s = applyDebateEvent(s, "debate_challenge_failed", { model_id: "model-a", error: "timeout" });
  const byAuthor = Object.fromEntries(s.challenges.map((c: { author_model_id: string }) => [c.author_model_id, c]));
  assert.equal(byAuthor["model-a"].status, "failed");
  assert.equal(byAuthor["model-b"].status, "completed");
});

// --- 13. Cancellation -----------------------------------------------------------

test("debate_cancelled moves the session to a terminal, non-active status", () => {
  const s = applyDebateEvent(session({ status: "running" }), "debate_cancelled", {});
  assert.equal(s.status, "cancelled");
  assert.equal(isDebateStatusActive(s.status), false);
  assert.equal(isDebateStatusTerminal(s.status), true);
});

// --- 14. Retry / attempt limit ---------------------------------------------------

test("retry is allowed below the attempt cap and blocked at or above it", () => {
  assert.equal(canStartNewDebateAttempt(0), true);
  assert.equal(canStartNewDebateAttempt(2), true);
  assert.equal(canStartNewDebateAttempt(3), false);
  assert.equal(remainingDebateAttempts(1), 2);
  assert.equal(remainingDebateAttempts(3), 0);
  assert.equal(remainingDebateAttempts(10), 0);
});

// --- 15. Historical attempts never mix with the latest ---------------------------

test("a snapshot for a different session id replaces local state outright, never merges", () => {
  const local = session({ id: "attempt-1", challenges: [{ id: "a", author_model_id: "model-a", status: "completed", challenges: [], created_at: "" }] });
  const remote = session({ id: "attempt-2", challenges: [] });
  const merged = mergeDebateSnapshot(local, remote);
  assert.equal(merged.id, "attempt-2");
  assert.deepEqual(merged.challenges, []);
});

test("a snapshot for the SAME session merges in whichever side has more resolved rows", () => {
  const local = session({
    id: "attempt-1",
    challenges: [{ id: "a", author_model_id: "model-a", status: "completed", challenges: [], created_at: "" }],
  });
  const remote = session({ id: "attempt-1", challenges: [] });
  const merged = mergeDebateSnapshot(local, remote);
  assert.equal(merged.challenges.length, 1);
});

// --- 16. Unknown usage is never shown as confirmed zero --------------------------

test("usage display distinguishes calls with unknown usage from zero cost", () => {
  const s = session({
    usage: { total_tokens_input: 10, total_tokens_output: 5, total_cost_usd: 0.002, calls_with_unknown_usage: 1 },
  });
  const usage = debateUsageDisplay(s);
  assert.equal(usage.hasUnknownUsage, true);
  assert.equal(usage.callsWithUnknownUsage, 1);
  assert.equal(usage.totalCostUsd, 0.002);
});

test("usage display with no unknown calls reports hasUnknownUsage=false", () => {
  const s = session({
    usage: { total_tokens_input: 0, total_tokens_output: 0, total_cost_usd: 0, calls_with_unknown_usage: 0 },
  });
  assert.equal(debateUsageDisplay(s).hasUnknownUsage, false);
});

// --- Idempotent event application (reconnection / replay safety) ----------------

test("re-applying the same challenge-completed event does not duplicate the row", () => {
  let s = session();
  const event = { model_id: "model-a", challenges: [], tokens_input: 5, tokens_output: 5, cost_usd: 0.001, error: null };
  s = applyDebateEvent(s, "debate_challenge_completed", event);
  s = applyDebateEvent(s, "debate_challenge_completed", event);
  assert.equal(s.challenges.length, 1);
});

test("grouping challenge rows by target never mixes two different target models", () => {
  const s = session({
    challenges: [
      {
        id: "a",
        author_model_id: "model-a",
        status: "completed",
        challenges: [
          { challenge_id: "c1", target_model_id: "model-b", issue: "x", reasoning: "y", severity: "low" },
          { challenge_id: "c2", target_model_id: "model-c", issue: "x", reasoning: "y", severity: "low" },
        ],
        created_at: "",
      },
    ],
  });
  const grouped = groupRowsByTarget(buildChallengeRows(s));
  assert.deepEqual([...grouped.keys()].sort(), ["model-b", "model-c"]);
  assert.equal(grouped.get("model-b")!.length, 1);
  assert.equal(grouped.get("model-c")!.length, 1);
});
