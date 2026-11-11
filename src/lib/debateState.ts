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
