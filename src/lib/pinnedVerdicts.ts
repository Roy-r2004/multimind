import type { SelectionLocator } from "./verdictSelectionNavigation.ts";
import type { ApiTurn } from "@/lib/api/types";

export type PinnedVerdict = {
  id?: string;
  verdictId: string;
  turnId: string;
  answerId?: string | null;
  pinType?: "verdict" | "selection" | "answer";
  selectedText?: string | null;
  selectedHtml?: string | null;
  selectionLocator?: SelectionLocator | null;
};

export type ApiPinRow = {
  id?: string;
  pin_type?: "verdict" | "selection" | "answer";
  selected_text?: string | null;
  selected_html?: string | null;
  selection_locator?: SelectionLocator | null;
  verdict_id?: string;
  answer_id?: string;
  turn_id: string;
};

export function pinVerdictPath(chatId: string, verdictId: string): string {
  return `/chats/${chatId}/pinned-verdicts/${verdictId}`;
}

export function verdictPinRequest(
  chatId: string,
  verdictId: string,
  action: "pin" | "unpin",
): { path: string; method: "PUT" | "DELETE" } {
  return {
    path: pinVerdictPath(chatId, verdictId),
    method: action === "pin" ? "PUT" : "DELETE",
  };
}

export function isVerdictPinned(pins: PinnedVerdict[], verdictId: string | undefined): boolean {
  return Boolean(
    verdictId &&
      pins.some(
        (pin) => pin.verdictId === verdictId && !pin.answerId && pin.pinType !== "selection",
      ),
  );
}

export function answerPinPath(chatId: string, answerId: string): string {
  return `/chats/${chatId}/pinned-answers/${answerId}`;
}

export function answerPinRequest(
  chatId: string,
  answerId: string,
  action: "pin" | "unpin",
): { path: string; method: "PUT" | "DELETE" } {
  return {
    path: answerPinPath(chatId, answerId),
    method: action === "pin" ? "PUT" : "DELETE",
  };
}

export function isAnswerPinned(pins: PinnedVerdict[], answerId: string | undefined): boolean {
  return Boolean(
    answerId && pins.some((pin) => pin.answerId === answerId && pin.pinType === "answer"),
  );
}

export function dropPinsForTurns(pins: PinnedVerdict[], turnIds: string[]): PinnedVerdict[] {
  const drop = new Set(turnIds);
  return pins.filter((pin) => !drop.has(pin.turnId));
}

export function pinnedMenuLabel(pins: PinnedVerdict[]): string {
  const hasAnswer = pins.some((pin) => pin.answerId);
  const hasVerdict = pins.some((pin) => pin.verdictId && !pin.answerId);
  if (hasAnswer && !hasVerdict) return "Pinned answers";
  if (hasAnswer && hasVerdict) return "Pinned";
  return "Pinned verdicts";
}

export function partitionPinsForApi(pins: PinnedVerdict[]): {
  pinned_verdicts: ApiPinRow[];
  pinned_answers: ApiPinRow[];
} {
  const pinned_verdicts: ApiPinRow[] = [];
  const pinned_answers: ApiPinRow[] = [];
  for (const pin of pins) {
    const row: ApiPinRow = {
      id: pin.id,
      pin_type: pin.pinType === "selection" ? "selection" : pin.answerId ? "answer" : "verdict",
      selected_text: pin.selectedText,
      selected_html: pin.selectedHtml,
      selection_locator: pin.selectionLocator,
      turn_id: pin.turnId,
    };
    if (pin.answerId) {
      pinned_answers.push({ ...row, answer_id: pin.answerId });
    } else if (pin.verdictId) {
      pinned_verdicts.push({ ...row, verdict_id: pin.verdictId });
    }
  }
  return { pinned_verdicts, pinned_answers };
}

export function pinnedVerdictLabel(pin: PinnedVerdict, turns: ApiTurn[]): string {
  if (pin.pinType === "selection") {
    const label = (pin.selectedText ?? "").replace(/\s+/g, " ").trim();
    return label.length > 64 ? `${label.slice(0, 63)}…` : label || "Pinned selection";
  }
  const visibleIndex = turns.findIndex((turn) => turn.id === pin.turnId);
  if (pin.pinType === "answer" || pin.answerId) {
    return visibleIndex >= 0 ? `Answer — Turn ${visibleIndex + 1}` : "Pinned answer";
  }
  return visibleIndex >= 0 ? `Verdict — Turn ${visibleIndex + 1}` : "Pinned verdict";
}

export function buildPinnedVerdictMenuItems(pins: PinnedVerdict[], turns: ApiTurn[]) {
  return pins.map((pin) => ({ ...pin, label: pinnedVerdictLabel(pin, turns) }));
}
