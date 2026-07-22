import type { ApiSavedVerdict, ApiTurn } from "./api/types";

export type VerdictBookmarkState = {
  visible: boolean;
  verdictId: string | null;
  saved: boolean;
  disabled: boolean;
  label: "Save Verdict" | "Remove saved Verdict";
  title: "Save Verdict" | "Remove saved Verdict";
  filled: boolean;
};

export function getVerdictBookmarkState(
  turn: ApiTurn,
  pendingVerdictIds: Set<string>,
): VerdictBookmarkState {
  const verdictId = turn.verdict?.id || null;
  const visible = Boolean(
    verdictId && turn.verdict?.text && (turn.status === "completed" || turn.status === "partial"),
  );
  const saved = Boolean(turn.verdict?.saved);
  const label = saved ? "Remove saved Verdict" : "Save Verdict";
  return {
    visible,
    verdictId,
    saved,
    disabled: Boolean(verdictId && pendingVerdictIds.has(verdictId)),
    label,
    title: label,
    filled: saved,
  };
}

export function updateVerdictSavedInTurns(
  turns: ApiTurn[],
  verdictId: string,
  saved: boolean,
): ApiTurn[] {
  return turns.map((turn) =>
    turn.verdict?.id === verdictId ? { ...turn, verdict: { ...turn.verdict, saved } } : turn,
  );
}

export function removeSavedVerdictBySourceId(
  items: ApiSavedVerdict[],
  sourceVerdictId: string,
): ApiSavedVerdict[] {
  return items.filter((item) => item.source_verdict_id !== sourceVerdictId);
}

export function restoreSavedVerdictItem(
  items: ApiSavedVerdict[],
  item: ApiSavedVerdict,
): ApiSavedVerdict[] {
  if (items.some((current) => current.id === item.id)) return items;
  return [item, ...items].sort((a, b) => b.saved_at.localeCompare(a.saved_at));
}

export type SavedVerdictCardView = {
  title: string;
  prompt: string;
  verdictText: string;
  verdictReason: string;
  modelId: string;
  strategy: string;
  savedAt: string;
  canOpenOriginalChat: boolean;
};

export function savedVerdictCardView(item: ApiSavedVerdict): SavedVerdictCardView {
  return {
    title: item.source_chat_title,
    prompt: item.source_user_message,
    verdictText: item.verdict_text,
    verdictReason: item.verdict_reason,
    modelId: item.verdict_model_id,
    strategy: item.strategy,
    savedAt: item.saved_at,
    canOpenOriginalChat: Boolean(item.original_chat_exists && item.source_chat_id),
  };
}
