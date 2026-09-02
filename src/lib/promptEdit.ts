/** Helpers for editing and regenerating user prompts. */

const GENERATING_STATUSES = new Set(["pending", "running"]);

export const LATER_TURNS_EDIT_WARNING =
  "Editing this message will regenerate this turn and remove later messages from the active conversation.";

function isGeneratingStatus(status: string): boolean {
  return GENERATING_STATUSES.has(String(status).toLowerCase());
}

export function conversationHasGeneratingTurn(turns: Array<{ status: string }>): boolean {
  return turns.some((item) => isGeneratingStatus(item.status));
}

export function canEditUserPromptWhen(
  turn: { status: string },
  conversationGenerating: boolean,
): boolean {
  if (isGeneratingStatus(turn.status)) return false;
  if (conversationGenerating) return false;
  return true;
}

export function canEditUserPrompt(
  turn: { status: string },
  turns: Array<{ status: string }>,
): boolean {
  return canEditUserPromptWhen(turn, conversationHasGeneratingTurn(turns));
}

export function countLaterTurns(
  turns: Array<{ id: string; created_at: string }>,
  turnId: string,
): number {
  const index = turns.findIndex((turn) => turn.id === turnId);
  if (index < 0) return 0;
  return Math.max(0, turns.length - index - 1);
}

export function canSubmitEditedPrompt(
  _original: string,
  draft: string,
  submitting: boolean,
): boolean {
  if (submitting) return false;
  return Boolean(draft.trim());
}

export function appendTranscriptToPrompt(current: string, transcriptValue: string): string {
  const transcript = transcriptValue.trim();
  if (!transcript) return current;

  const existing = current.trimEnd();
  return existing ? `${existing}\n\n${transcript}` : transcript;
}
