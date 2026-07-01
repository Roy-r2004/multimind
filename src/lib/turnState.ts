/** Shared turn state helpers for chat UI and background streaming. */

import type { ApiTurn } from "@/lib/api/types";

function coerceText(value: unknown): string {
  if (Array.isArray(value)) {
    return value
      .map((item) => {
        const s = String(item).trim();
        if (!s) return "";
        return s.startsWith("•") || s.startsWith("-") || s.startsWith("*") ? s : `• ${s}`;
      })
      .filter(Boolean)
      .join("\n");
  }
  return String(value ?? "");
}

export function applyStreamEvent(turn: ApiTurn, event: string, data: Record<string, unknown>): ApiTurn {
  if (event === "model_answer_started") {
    return {
      ...turn,
      status: "running",
      model_answers: turn.model_answers.map((a) =>
        a.model_id === data.model_id ? { ...a, status: "running" } : a,
      ),
    };
  }
  if (event === "model_answer_completed") {
    return {
      ...turn,
      model_answers: turn.model_answers.map((a) =>
        a.model_id === data.model_id
          ? {
              ...a,
              text: String(data.text ?? ""),
              confidence: Number(data.confidence ?? a.confidence),
              status: "completed",
              tokens_input: Number(data.tokens_input ?? 0),
              tokens_output: Number(data.tokens_output ?? 0),
              cost_usd: Number(data.cost_usd ?? 0),
            }
          : a,
      ),
    };
  }
  if (event === "model_answer_failed") {
    return {
      ...turn,
      model_answers: turn.model_answers.map((a) =>
        a.model_id === data.model_id
          ? { ...a, status: "failed", error_message: String(data.error ?? "Failed") }
          : a,
      ),
    };
  }
  if (event === "verdict_completed") {
    return {
      ...turn,
      verdict: {
        model_id: String(data.model_id ?? turn.verdict_model),
        strategy: turn.strategy,
        text: String(data.text ?? ""),
        reason: String(data.reason ?? ""),
        tokens_input: Number(data.tokens_input ?? 0),
        tokens_output: Number(data.tokens_output ?? 0),
        cost_usd: Number(data.cost_usd ?? 0),
      },
    };
  }
  if (event === "decision_insurance_completed") {
    return {
      ...turn,
      decision_insurance: {
        best_case: coerceText(data.best_case),
        worst_case: coerceText(data.worst_case),
        risk_level: coerceText(data.risk_level),
        potential_loss: coerceText(data.potential_loss),
        mitigation_plan: coerceText(data.mitigation_plan),
        tokens_input: Number(data.tokens_input ?? 0),
        tokens_output: Number(data.tokens_output ?? 0),
        cost_usd: Number(data.cost_usd ?? 0),
      },
    };
  }
  return turn;
}

export function mergeTurnFromApi(local: ApiTurn, remote: ApiTurn): ApiTurn {
  const mergedAnswers = local.model_answers.map((localA) => {
    const remoteA = remote.model_answers.find((r) => r.model_id === localA.model_id);
    if (!remoteA) return localA;
    if (localA.status === "completed" && localA.text) return localA;
    if (remoteA.status === "completed" && remoteA.text) return remoteA;
    if (localA.status === "running" && remoteA.status === "pending") return localA;
    return remoteA;
  });
  return {
    ...remote,
    model_answers: mergedAnswers,
    verdict: remote.verdict ?? local.verdict,
    decision_insurance: remote.decision_insurance ?? local.decision_insurance,
  };
}

export function upsertTurn(list: ApiTurn[], turn: ApiTurn): ApiTurn[] {
  const idx = list.findIndex((t) => t.id === turn.id);
  if (idx === -1) return [...list, turn];
  return list.map((t, i) => (i === idx ? mergeTurnFromApi(t, turn) : t));
}

export function mergeTurnLists(apiTurns: ApiTurn[], cachedTurns: ApiTurn[]): ApiTurn[] {
  let merged = [...apiTurns];
  for (const cached of cachedTurns) {
    merged = upsertTurn(merged, cached);
  }
  return merged.sort((a, b) => a.created_at.localeCompare(b.created_at));
}
