import assert from "node:assert/strict";
import test from "node:test";

import {
  applyDebateEvent,
  buildChallengeRows,
  canStartNewDebateAttempt,
  challengeRowKey,
  debateEligibility,
  debateFailureDetails,
  debatePlainFindings,
  debateSummaryCounts,
  debateUsageDisplay,
  groupRowsByTarget,
  isDebateStatusActive,
  isDebateStatusTerminal,
  isZeroChallengeDebate,
  mergeDebateSnapshot,
  modelDebateOutcome,
  remainingDebateAttempts,
  safeFailureReason,
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
    usage: {
      total_tokens_input: 0,
      total_tokens_output: 0,
      total_cost_usd: 0,
      calls_with_unknown_usage: 0,
    },
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
  const result = debateEligibility({
    hasVerdict: false,
    completedAnswers: [answer("a"), answer("b")],
  });
  assert.equal(result.eligible, false);
  assert.match((result as { reason: string }).reason, /Final Verdict/);
});

test("eligibility requires at least two completed answers", () => {
  const result = debateEligibility({ hasVerdict: true, completedAnswers: [answer("a")] });
  assert.equal(result.eligible, false);
  assert.match((result as { reason: string }).reason, /at least two/);
});

test("eligibility passes with a verdict and two completed answers", () => {
  const result = debateEligibility({
    hasVerdict: true,
    completedAnswers: [answer("a"), answer("b")],
  });
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
        challenges: [
          {
            challenge_id: "c1",
            target_model_id: "model-b",
            issue: "x",
            reasoning: "y",
            severity: "low",
          },
        ],
        created_at: "",
      },
      {
        id: "c",
        author_model_id: "model-c",
        status: "completed",
        challenges: [
          {
            challenge_id: "c1",
            target_model_id: "model-b",
            issue: "z",
            reasoning: "w",
            severity: "medium",
          },
        ],
        created_at: "",
      },
    ],
    responses: [
      {
        id: "b",
        author_model_id: "model-b",
        status: "completed",
        responses: [
          {
            challenge_id: "c1",
            challenger_model_id: "model-a",
            stance: "defends",
            explanation: "re a",
          },
          {
            challenge_id: "c1",
            challenger_model_id: "model-c",
            stance: "concedes",
            explanation: "re c",
            updated_position: "fixed",
          },
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
        challenges: [
          {
            challenge_id: "c1",
            target_model_id: "model-b",
            issue: "x",
            reasoning: "y",
            severity: "low",
          },
        ],
        created_at: "",
      },
      {
        id: "c",
        author_model_id: "model-c",
        status: "completed",
        challenges: [
          {
            challenge_id: "c1",
            target_model_id: "model-b",
            issue: "z",
            reasoning: "w",
            severity: "medium",
          },
        ],
        created_at: "",
      },
    ],
    responses: [
      {
        id: "b",
        author_model_id: "model-b",
        status: "completed",
        responses: [
          {
            challenge_id: "c1",
            challenger_model_id: "model-a",
            stance: "defends",
            explanation: "re a",
          },
          {
            challenge_id: "c1",
            challenger_model_id: "model-c",
            stance: "concedes",
            explanation: "re c",
            updated_position: "fixed",
          },
        ],
        created_at: "",
      },
    ],
  });
  const byChallenger = Object.fromEntries(
    buildChallengeRows(s).map((r) => [r.challengerModelId, r]),
  );
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
    challenges: [
      { id: "a", author_model_id: "model-a", status: "failed", challenges: null, created_at: "" },
    ],
  });
  assert.equal(modelDebateOutcome("model-a", s), "failed");
});

test("a model that raised no challenges but wasn't itself challenged is unchallenged, not failed", () => {
  const s = session({
    challenges: [
      { id: "a", author_model_id: "model-a", status: "completed", challenges: [], created_at: "" },
    ],
  });
  assert.equal(modelDebateOutcome("model-a", s), "unchallenged");
});

test("debate_challenge_failed applies a per-model failure without touching other models", () => {
  let s = session({
    challenges: [
      { id: "b", author_model_id: "model-b", status: "completed", challenges: [], created_at: "" },
    ],
  });
  s = applyDebateEvent(s, "debate_challenge_failed", { model_id: "model-a", error: "timeout" });
  const byAuthor = Object.fromEntries(
    s.challenges.map((c: { author_model_id: string }) => [c.author_model_id, c]),
  );
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
  const local = session({
    id: "attempt-1",
    challenges: [
      { id: "a", author_model_id: "model-a", status: "completed", challenges: [], created_at: "" },
    ],
  });
  const remote = session({ id: "attempt-2", challenges: [] });
  const merged = mergeDebateSnapshot(local, remote);
  assert.equal(merged.id, "attempt-2");
  assert.deepEqual(merged.challenges, []);
});

test("a snapshot for the SAME session merges in whichever side has more resolved rows", () => {
  const local = session({
    id: "attempt-1",
    challenges: [
      { id: "a", author_model_id: "model-a", status: "completed", challenges: [], created_at: "" },
    ],
  });
  const remote = session({ id: "attempt-1", challenges: [] });
  const merged = mergeDebateSnapshot(local, remote);
  assert.equal(merged.challenges.length, 1);
});

// --- 16. Unknown usage is never shown as confirmed zero --------------------------

test("usage display distinguishes calls with unknown usage from zero cost", () => {
  const s = session({
    usage: {
      total_tokens_input: 10,
      total_tokens_output: 5,
      total_cost_usd: 0.002,
      calls_with_unknown_usage: 1,
    },
  });
  const usage = debateUsageDisplay(s);
  assert.equal(usage.hasUnknownUsage, true);
  assert.equal(usage.callsWithUnknownUsage, 1);
  assert.equal(usage.totalCostUsd, 0.002);
});

test("usage display with no unknown calls reports hasUnknownUsage=false", () => {
  const s = session({
    usage: {
      total_tokens_input: 0,
      total_tokens_output: 0,
      total_cost_usd: 0,
      calls_with_unknown_usage: 0,
    },
  });
  assert.equal(debateUsageDisplay(s).hasUnknownUsage, false);
});

// --- Idempotent event application (reconnection / replay safety) ----------------

test("re-applying the same challenge-completed event does not duplicate the row", () => {
  let s = session();
  const event = {
    model_id: "model-a",
    challenges: [],
    tokens_input: 5,
    tokens_output: 5,
    cost_usd: 0.001,
    error: null,
  };
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
          {
            challenge_id: "c1",
            target_model_id: "model-b",
            issue: "x",
            reasoning: "y",
            severity: "low",
          },
          {
            challenge_id: "c2",
            target_model_id: "model-c",
            issue: "x",
            reasoning: "y",
            severity: "low",
          },
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

// --- Debate Findings UX: summary counts, plain findings, failure details ----

function challengeRow(author: string, items: Record<string, unknown>[]) {
  return {
    id: author,
    author_model_id: author,
    status: "completed",
    challenges: items,
    created_at: "",
  };
}

test("summary counts are read directly from persisted findings, matching a real four-challenge debate", () => {
  // Mirrors the actual production session investigated in this task: 3
  // models, 4 challenges raised, 2 concessions, 2 revisions, 0 unresolved.
  const s = session({
    challenges: [
      challengeRow("gpt-4.1", [
        {
          challenge_id: "c1",
          target_model_id: "claude",
          issue: "x",
          reasoning: "y",
          severity: "medium",
        },
        {
          challenge_id: "c2",
          target_model_id: "gemini",
          issue: "x",
          reasoning: "y",
          severity: "medium",
        },
      ]),
      challengeRow("gemini", [
        {
          challenge_id: "c1",
          target_model_id: "gpt-4.1",
          issue: "x",
          reasoning: "y",
          severity: "medium",
        },
        {
          challenge_id: "c2",
          target_model_id: "claude",
          issue: "x",
          reasoning: "y",
          severity: "medium",
        },
      ]),
    ],
    corrections: [
      {
        description: "a",
        model_id: "claude",
        challenge_id: "c1",
        stance: "concedes",
        updated_position: "u1",
      },
      {
        description: "b",
        model_id: "claude",
        challenge_id: "c2",
        stance: "concedes",
        updated_position: "u2",
      },
      {
        description: "c",
        model_id: "gemini",
        challenge_id: "c2",
        stance: "revises",
        updated_position: "u3",
      },
      {
        description: "d",
        model_id: "gpt-4.1",
        challenge_id: "c1",
        stance: "revises",
        updated_position: "u4",
      },
    ],
    unresolved_disagreements: [],
  });
  const counts = debateSummaryCounts(s);
  assert.deepEqual(counts, { challengesRaised: 4, conceded: 2, revised: 2, unresolved: 0 });
});

test("unresolved count combines both still-defended and never-answered challenges", () => {
  const s = session({
    unresolved_disagreements: [
      {
        description: "a",
        challenge_id: "c1",
        challenger_model_id: "x",
        target_model_id: "y",
        severity: "low",
      },
      {
        description: "b",
        challenge_id: "c2",
        challenger_model_id: "x",
        target_model_id: "z",
        severity: "low",
      },
    ],
  });
  assert.equal(debateSummaryCounts(s).unresolved, 2);
});

// --- Explaining PARTIAL from real persisted failure data ------------------------

test("debateFailureDetails reports exactly the model/stage/error a real Round-2 timeout left behind", () => {
  // The exact shape of the production bug this task diagnosed: Claude's
  // Round-2 challenge call timed out; its row persists as status=failed
  // with a real (now non-empty, post-fix) error_message.
  const s = session({
    status: "partial",
    challenges: [
      {
        id: "claude",
        author_model_id: "claude",
        status: "failed",
        challenges: null,
        error_message: "TimeoutError (no further detail)",
        created_at: "",
      },
      challengeRow("gpt-4.1", []),
      challengeRow("gemini", []),
    ],
  });
  const details = debateFailureDetails(s);
  assert.deepEqual(details, [
    { modelId: "claude", stage: "challenge", rawError: "TimeoutError (no further detail)" },
  ]);
});

test("debateFailureDetails returns nothing for a fully successful debate", () => {
  const s = session({
    challenges: [challengeRow("gpt-4.1", []), challengeRow("gemini", [])],
  });
  assert.deepEqual(debateFailureDetails(s), []);
});

test("safeFailureReason never echoes a raw message that looks credential-shaped", () => {
  assert.equal(
    safeFailureReason("Invalid API key: sk-abc123"),
    "failed due to a configuration issue",
  );
  assert.equal(
    safeFailureReason("Authorization header missing"),
    "failed due to a configuration issue",
  );
});

test("safeFailureReason recognizes common failure shapes without inventing detail", () => {
  assert.equal(safeFailureReason(""), "failed without returning a specific reason");
  assert.equal(safeFailureReason("TimeoutError (no further detail)"), "took too long to respond");
  // The backend's actual message format for a per-call timeout since the
  // reliability fix (states the real bound hit, no bare "TimeoutError").
  assert.equal(safeFailureReason("model did not respond within 75s"), "took too long to respond");
  assert.equal(
    safeFailureReason("OpenRouter error (503): Unavailable"),
    "the model provider was temporarily unavailable",
  );
  assert.equal(
    safeFailureReason("response was not valid or recoverable JSON"),
    "returned a response that could not be understood",
  );
});

// --- Plain-language findings, derived only from persisted data ------------------

test("plain findings separate problems, acknowledgements, and open considerations", () => {
  const s = session({
    challenges: [
      challengeRow("gpt-4.1", [
        {
          challenge_id: "c1",
          target_model_id: "claude",
          issue: "Claude understated a risk.",
          reasoning: "y",
          severity: "medium",
        },
      ]),
    ],
    corrections: [
      {
        description: "x",
        model_id: "claude",
        challenge_id: "c1",
        stance: "concedes",
        updated_position: "Fixed.",
      },
    ],
    unresolved_disagreements: [
      {
        description: "Still disputed.",
        challenge_id: "c9",
        challenger_model_id: "a",
        target_model_id: "b",
        severity: "low",
      },
    ],
    risks: [{ description: "A real risk.", severity: "high", raised_by_model_ids: ["gpt-4.1"] }],
    uncertainties: [{ description: "Not fully sure.", related_model_ids: ["gemini"] }],
  });
  const findings = debatePlainFindings(s);
  assert.deepEqual(findings.problems, ["Claude understated a risk."]);
  assert.deepEqual(findings.acknowledgements, ["x"]);
  assert.deepEqual(findings.considerations, ["Still disputed.", "A real risk.", "Not fully sure."]);
});

test("plain findings never fabricate a problem that wasn't actually raised (zero-challenge case)", () => {
  const s = session({ challenges: [challengeRow("gpt-4.1", []), challengeRow("gemini", [])] });
  const findings = debatePlainFindings(s);
  assert.deepEqual(findings, { problems: [], acknowledgements: [], considerations: [] });
});
