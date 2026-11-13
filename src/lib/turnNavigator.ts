import type { ApiTurn } from "@/lib/api/types";

export const TURN_NAVIGATOR_PREVIEW_MAX_LENGTH = 72;
/** Only the leading slice of a question is ever read; huge prompts never get fully scanned. */
const PREVIEW_SOURCE_SCAN_LIMIT = TURN_NAVIGATOR_PREVIEW_MAX_LENGTH * 4;

export type TurnNavigatorItem = {
  turnId: string;
  index: number;
  preview: string;
};

/**
 * Collapse whitespace/newlines and truncate to a single-line preview without
 * ever processing more than a small prefix of a (possibly huge) question.
 */
export function previewFromQuestionText(
  text: string | null | undefined,
  maxLength: number = TURN_NAVIGATOR_PREVIEW_MAX_LENGTH,
): string {
  if (!text) return "";
  const prefix = text.slice(0, PREVIEW_SOURCE_SCAN_LIMIT);
  const normalized = prefix.replace(/\s+/g, " ").trim();
  if (!normalized) return "";
  if (normalized.length <= maxLength) {
    return text.length > PREVIEW_SOURCE_SCAN_LIMIT ? `${normalized}…` : normalized;
  }
  return `${normalized.slice(0, maxLength).trimEnd()}…`;
}

function attachmentsOnlyPreview(turn: ApiTurn): string {
  const count = turn.attachments?.length ?? 0;
  if (count === 0) return "Untitled question";
  return count === 1 ? "1 attachment" : `${count} attachments`;
}

/** Preview text for a turn: question text when present, else an attachment summary. */
export function turnNavigatorPreview(turn: ApiTurn): string {
  const fromText = previewFromQuestionText(turn.user_message);
  return fromText || attachmentsOnlyPreview(turn);
}

export function buildTurnNavigatorItems(turns: ApiTurn[]): TurnNavigatorItem[] {
  return turns.map((turn, index) => ({
    turnId: turn.id,
    index,
    preview: turnNavigatorPreview(turn),
  }));
}

export function clampTurnNavigatorIndex(index: number, count: number): number {
  if (count <= 0) return -1;
  if (index < 0) return 0;
  if (index > count - 1) return count - 1;
  return index;
}

export function turnNavigatorIndexById(items: TurnNavigatorItem[], turnId: string | null): number {
  if (!turnId) return -1;
  return items.findIndex((item) => item.turnId === turnId);
}

export function nextTurnNavigatorIndex(currentIndex: number, count: number): number {
  if (count <= 0) return -1;
  if (currentIndex < 0) return 0;
  return Math.min(count - 1, currentIndex + 1);
}

export function previousTurnNavigatorIndex(currentIndex: number, count: number): number {
  if (count <= 0) return -1;
  if (currentIndex < 0) return count - 1;
  return Math.max(0, currentIndex - 1);
}

/**
 * After the turn list changes (new/edited/deleted turns), pick the index to
 * keep showing: follow the same turn id when it still exists, otherwise fall
 * back to the same position (clamped) so the control never points past the end.
 */
export function reconcileTurnNavigatorIndex(
  previousTurnId: string | null,
  previousIndex: number,
  items: TurnNavigatorItem[],
): number {
  const byId = turnNavigatorIndexById(items, previousTurnId);
  if (byId >= 0) return byId;
  return clampTurnNavigatorIndex(previousIndex, items.length);
}
