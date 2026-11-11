/** Pure Debate Mode state helpers — eligibility, event merging, composite-key
 * grouping, and findings shaping. No network/React code lives here (mirrors
 * turnState.ts), so this file only ever imports @/ types (erased at compile
 * time), which keeps it directly unit-testable with plain `node --test`.
 */

import type {
  ApiDebateChallenge,
  ApiDebateChallengeItem,
  ApiDebateResponse,
  ApiDebateResponseItem,
  ApiDebateSession,
  ApiModelAnswer,
} from "@/lib/api/types";

export const ACTIVE_DEBATE_STATUSES = new Set(["pending", "running"]);
export const DEBATE_MAX_ATTEMPTS_PER_TURN = 3;

export function isDebateStatusActive(status: string): boolean {
  return ACTIVE_DEBATE_STATUSES.has(status.toLowerCase());
}

export function isDebateStatusTerminal(status: string): boolean {
  return !isDebateStatusActive(status);
}

/** Why Debate is or isn't allowed right now — never auto-started, and the
 * caller must be able to explain an ineligible state to the user. */
export function debateEligibility(args: {
  hasVerdict: boolean;
  completedAnswers: ApiModelAnswer[];
}): { eligible: true } | { eligible: false; reason: string } {
  if (!args.hasVerdict) {
    return { eligible: false, reason: "Debate opens once the Final Verdict is ready." };
  }
  if (args.completedAnswers.length < 2) {
    return {
      eligible: false,
      reason: "Debate needs at least two completed AI answers to run a peer review.",
    };
  }
  return { eligible: true };
}

export function canStartNewDebateAttempt(attemptCount: number): boolean {
  return attemptCount < DEBATE_MAX_ATTEMPTS_PER_TURN;
}

export function remainingDebateAttempts(attemptCount: number): number {
  return Math.max(0, DEBATE_MAX_ATTEMPTS_PER_TURN - attemptCount);
}

/** One row: a single challenge (raised by `challengerModelId` against
 * `targetModelId`) plus its resolution, if any — identified by the
 * composite (challenger_model_id, challenge_id), since challenge_id alone
 * may legitimately collide across different reviewing models. */
export type DebateChallengeRow = {
  key: string;
  challengerModelId: string;
  targetModelId: string;
  challenge: ApiDebateChallengeItem;
  response: ApiDebateResponseItem | null;
};

export function challengeRowKey(challengerModelId: string, challengeId: string): string {
  return `${challengerModelId}::${challengeId}`;
}

function responsesByKey(responses: ApiDebateResponse[]): Map<string, ApiDebateResponseItem> {
  const map = new Map<string, ApiDebateResponseItem>();
  for (const response of responses) {
    for (const item of response.responses ?? []) {
      map.set(challengeRowKey(item.challenger_model_id, item.challenge_id), item);
    }
  }
  return map;
}

/** Flattens every challenge across all challengers into rows, each paired
 * with its response (if the challenged model has replied yet). Never mixes
 * two challenges that merely share a bare challenge_id. */
export function buildChallengeRows(session: ApiDebateSession): DebateChallengeRow[] {
  const byKey = responsesByKey(session.responses ?? []);
  const rows: DebateChallengeRow[] = [];
  for (const challenge of session.challenges ?? []) {
    for (const item of challenge.challenges ?? []) {
      const key = challengeRowKey(challenge.author_model_id, item.challenge_id);
      rows.push({
        key,
        challengerModelId: challenge.author_model_id,
        targetModelId: item.target_model_id,
        challenge: item,
        response: byKey.get(key) ?? null,
      });
    }
  }
  return rows;
}

export function groupRowsByTarget(rows: DebateChallengeRow[]): Map<string, DebateChallengeRow[]> {
  const grouped = new Map<string, DebateChallengeRow[]>();
  for (const row of rows) {
    const list = grouped.get(row.targetModelId) ?? [];
    list.push(row);
    grouped.set(row.targetModelId, list);
  }
  return grouped;
}

/** A model can appear in exactly one of these three buckets, never two:
 * failed outright, challenged (zero or more challenges, resolved or not),
 * or unchallenged. "Unchallenged" is never displayed as agreement. */
export function modelDebateOutcome(
  modelId: string,
  session: ApiDebateSession,
): "failed" | "challenged" | "unchallenged" {
  const challengeEntry = (session.challenges ?? []).find((c) => c.author_model_id === modelId);
  if (challengeEntry?.status === "failed") return "failed";
  const wasChallenged = (session.challenges ?? []).some((c) =>
    (c.challenges ?? []).some((item) => item.target_model_id === modelId),
  );
  return wasChallenged ? "challenged" : "unchallenged";
}

export function isZeroChallengeDebate(session: ApiDebateSession): boolean {
  return buildChallengeRows(session).length === 0;
}

export type DebateUsageDisplay = {
  totalCostUsd: number;
  totalTokensInput: number;
  totalTokensOutput: number;
  callsWithUnknownUsage: number;
  hasUnknownUsage: boolean;
};

export function debateUsageDisplay(session: ApiDebateSession): DebateUsageDisplay {
  const usage = session.usage;
  return {
    totalCostUsd: usage?.total_cost_usd ?? 0,
    totalTokensInput: usage?.total_tokens_input ?? 0,
    totalTokensOutput: usage?.total_tokens_output ?? 0,
    callsWithUnknownUsage: usage?.calls_with_unknown_usage ?? 0,
    hasUnknownUsage: (usage?.calls_with_unknown_usage ?? 0) > 0,
  };
}

/** Applies one SSE lifecycle event onto a session snapshot. Always
 * idempotent against re-delivery/replay after a reconnect: challenge and
 * response rows are upserted by author/composite key, never appended
 * blindly, so seeing the same event twice (once from a missed-then-resent
 * SSE frame, once from a post-reconnect GET refetch) never duplicates a row. */
export function applyDebateEvent(
  session: ApiDebateSession,
  event: string,
  data: Record<string, unknown>,
): ApiDebateSession {
  if (event === "debate_started") {
    return { ...session, status: "running" };
  }

  if (event === "debate_challenge_completed" || event === "debate_challenge_failed") {
    const modelId = String(data.model_id ?? "");
    const next: ApiDebateChallenge = {
      id: modelId,
      author_model_id: modelId,
      status: event === "debate_challenge_failed" ? "failed" : "completed",
      challenges: (data.challenges as ApiDebateChallengeItem[] | null) ?? null,
      error_message: (data.error as string | null) ?? null,
      tokens_input: (data.tokens_input as number | null) ?? null,
      tokens_output: (data.tokens_output as number | null) ?? null,
      cost_usd: (data.cost_usd as number | null) ?? null,
      created_at: new Date().toISOString(),
    };
    const existing = session.challenges ?? [];
    const withoutModel = existing.filter((c) => c.author_model_id !== modelId);
    return { ...session, challenges: [...withoutModel, next] };
  }

  if (event === "debate_response_completed" || event === "debate_response_failed") {
    const modelId = String(data.model_id ?? "");
    const next: ApiDebateResponse = {
      id: modelId,
      author_model_id: modelId,
      status: event === "debate_response_failed" ? "failed" : "completed",
      responses: (data.responses as ApiDebateResponseItem[] | null) ?? null,
      error_message: (data.error as string | null) ?? null,
      tokens_input: (data.tokens_input as number | null) ?? null,
      tokens_output: (data.tokens_output as number | null) ?? null,
      cost_usd: (data.cost_usd as number | null) ?? null,
      created_at: new Date().toISOString(),
    };
    const existing = session.responses ?? [];
    const withoutModel = existing.filter((r) => r.author_model_id !== modelId);
    return { ...session, responses: [...withoutModel, next] };
  }

  if (
    event === "debate_completed" ||
    event === "debate_partial" ||
    event === "debate_failed" ||
    event === "debate_cancelled"
  ) {
    const status = event.replace("debate_", "");
    return { ...session, status };
  }

  return session;
}

/** Merges a freshly-fetched snapshot (authoritative) with locally-applied
 * SSE events, so a reconnect's GET never discards progress a late event
 * already reflected, and a late event after the snapshot never resurrects
 * a session the snapshot already shows as terminal. */
export function mergeDebateSnapshot(
  local: ApiDebateSession | null,
  remote: ApiDebateSession,
): ApiDebateSession {
  if (!local || local.id !== remote.id) return remote;
  // Prefer whichever has strictly more resolved rows per side, then prefer
  // the remote (durable, authoritative) for everything else.
  const challenges =
    (local.challenges?.length ?? 0) > (remote.challenges?.length ?? 0)
      ? local.challenges
      : remote.challenges;
  const responses =
    (local.responses?.length ?? 0) > (remote.responses?.length ?? 0)
      ? local.responses
      : remote.responses;
  return { ...remote, challenges, responses };
}

// --- Plain-language summary (Debate Findings UX) ----------------------------

export type DebateSummaryCounts = {
  /** Total individual challenges raised, across every reviewing model. */
  challengesRaised: number;
  conceded: number;
  revised: number;
  /** Still defended OR never answered — both are "unresolved" from the
   * user's point of view, and the backend's own unresolved_disagreements
   * list already combines them (see app.llm.debate_orchestrator.reduce_findings). */
  unresolved: number;
};

/** Every count here is read directly from already-persisted findings —
 * never recomputed by inference, so it can never drift from what the
 * backend actually determined. */
export function debateSummaryCounts(session: ApiDebateSession): DebateSummaryCounts {
  const corrections = session.corrections ?? [];
  return {
    challengesRaised: buildChallengeRows(session).length,
    conceded: corrections.filter((c) => c.stance === "concedes").length,
    revised: corrections.filter((c) => c.stance === "revises").length,
    unresolved: (session.unresolved_disagreements ?? []).length,
  };
}

export type DebateFailureDetail = {
  modelId: string;
  stage: "challenge" | "response";
  rawError: string;
};

/** Every model/stage that actually failed, straight from the persisted
 * status + error_message columns — never inferred from an absence of data. */
export function debateFailureDetails(session: ApiDebateSession): DebateFailureDetail[] {
  const details: DebateFailureDetail[] = [];
  for (const c of session.challenges ?? []) {
    if (c.status === "failed") {
      details.push({
        modelId: c.author_model_id,
        stage: "challenge",
        rawError: c.error_message ?? "",
      });
    }
  }
  for (const r of session.responses ?? []) {
    if (r.status === "failed") {
      details.push({
        modelId: r.author_model_id,
        stage: "response",
        rawError: r.error_message ?? "",
      });
    }
  }
  return details;
}

/** A short, safe phrase for a raw provider/internal error string — never
 * shown verbatim, since a raw message could in principle echo request
 * details. Recognizes common failure shapes; anything else (or anything
 * that looks like it might contain a credential/token/key) falls back to
 * a generic phrase rather than risk leaking it. */
export function safeFailureReason(rawError: string): string {
  const trimmed = rawError.trim();
  if (!trimmed) return "failed without returning a specific reason";
  const lower = trimmed.toLowerCase();
  if (/\b(key|token|secret|authorization|bearer)\b/.test(lower)) {
    return "failed due to a configuration issue";
  }
  if (lower.includes("timeout") || lower.includes("timed out")) {
    return "took too long to respond";
  }
  if (lower.includes("503") || lower.includes("unavailable") || lower.includes("502")) {
    return "the model provider was temporarily unavailable";
  }
  if (lower.includes("json") || lower.includes("parse") || lower.includes("valid")) {
    return "returned a response that could not be understood";
  }
  if (lower.includes("unknown model")) {
    return "could not be reached (unknown model)";
  }
  return "the request failed";
}

export type DebatePlainFindings = {
  /** "What problems did the AIs identify?" */
  problems: string[];
  /** "What did the AIs change or acknowledge?" */
  acknowledgements: string[];
  /** "What should the user consider now?" */
  considerations: string[];
};

/** Deterministic, data-only presentation — never an extra LLM call, and
 * never a claim stronger than what was actually persisted. Phrased from
 * the model's own stated issue/description text, not a generated summary. */
export function debatePlainFindings(session: ApiDebateSession): DebatePlainFindings {
  const rows = buildChallengeRows(session);
  const problems = rows.map((r) => r.challenge.issue);
  const acknowledgements = (session.corrections ?? []).map((c) => c.description);
  const considerations = [
    ...(session.unresolved_disagreements ?? []).map((d) => d.description),
    ...(session.risks ?? []).map((r) => r.description),
    ...(session.uncertainties ?? []).map((u) => u.description),
  ];
  return { problems, acknowledgements, considerations };
}
