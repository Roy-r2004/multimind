import type { ApiTurn } from "@/lib/api/types";

export type PinnedVerdict = { verdictId: string; turnId: string };

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
  return Boolean(verdictId && pins.some((pin) => pin.verdictId === verdictId));
}

export function pinnedVerdictLabel(pin: PinnedVerdict, turns: ApiTurn[]): string {
  const visibleIndex = turns.findIndex((turn) => turn.id === pin.turnId);
  return visibleIndex >= 0 ? `Verdict — Turn ${visibleIndex + 1}` : "Pinned verdict";
}

export function buildPinnedVerdictMenuItems(pins: PinnedVerdict[], turns: ApiTurn[]) {
  return pins.map((pin) => ({ ...pin, label: pinnedVerdictLabel(pin, turns) }));
}
