/** Keeps turn streaming alive across route changes. */

import { streamTurn } from "@/lib/api/stream";
import { api } from "@/lib/api";
import { isRequestCancelled } from "@/lib/api/client";
import type { ApiTurn } from "@/lib/api/types";
import { applyStreamEvent, mergeTurnFromApi, mergeTurnLists } from "@/lib/turnState";

type Auth = { token: string; orgId?: string | null };

const turnsByChat = new Map<string, Map<string, ApiTurn>>();
const activeJobs = new Map<
  string,
  { chatId: string; controller: AbortController; promise: Promise<void>; stopping: boolean }
>();
const deletedTurns = new Set<string>();
const chatListeners = new Map<string, Set<(turns: ApiTurn[]) => void>>();
const runningListeners = new Map<string, Set<(running: boolean) => void>>();
const activeTurnListeners = new Map<string, Set<(turnId: string | null) => void>>();

function getChatTurnMap(chatId: string): Map<string, ApiTurn> {
  let map = turnsByChat.get(chatId);
  if (!map) {
    map = new Map();
    turnsByChat.set(chatId, map);
  }
  return map;
}

export function getChatTurns(chatId: string): ApiTurn[] {
  const map = turnsByChat.get(chatId);
  if (!map) return [];
  return Array.from(map.values()).sort((a, b) => a.created_at.localeCompare(b.created_at));
}

function isChatRunning(chatId: string): boolean {
  return [...activeJobs.values()].some((job) => job.chatId === chatId);
}

function getActiveTurnId(chatId: string): string | null {
  const activeIds = new Set(
    [...activeJobs.entries()].filter(([, job]) => job.chatId === chatId).map(([turnId]) => turnId),
  );
  const turns = getChatTurns(chatId);
  for (let index = turns.length - 1; index >= 0; index -= 1) {
    if (activeIds.has(turns[index].id)) return turns[index].id;
  }
  return null;
}

function emitChat(chatId: string) {
  const turns = getChatTurns(chatId);
  chatListeners.get(chatId)?.forEach((fn) => fn(turns));
  const running = isChatRunning(chatId);
  runningListeners.get(chatId)?.forEach((fn) => fn(running));
  const activeTurnId = getActiveTurnId(chatId);
  activeTurnListeners.get(chatId)?.forEach((fn) => fn(activeTurnId));
}

function updateTurn(chatId: string, turn: ApiTurn) {
  if (deletedTurns.has(turn.id)) return;
  getChatTurnMap(chatId).set(turn.id, turn);
  emitChat(chatId);
}

export function removeTurn(chatId: string, turnId: string) {
  deletedTurns.add(turnId);
  getChatTurnMap(chatId).delete(turnId);
  const job = activeJobs.get(turnId);
  if (job) {
    job.controller.abort();
    activeJobs.delete(turnId);
  }
  emitChat(chatId);
}

export function isTurnDeletedLocally(turnId: string): boolean {
  return deletedTurns.has(turnId);
}

export function seedChatTurns(chatId: string, turns: ApiTurn[]) {
  const map = getChatTurnMap(chatId);
  for (const turn of turns) {
    if (deletedTurns.has(turn.id)) continue;
    const existing = map.get(turn.id);
    map.set(turn.id, existing ? mergeTurnFromApi(existing, turn) : turn);
  }
  emitChat(chatId);
}

export function setVerdictSavedState(verdictId: string, saved: boolean) {
  const changedChatIds = new Set<string>();
  for (const [chatId, map] of turnsByChat.entries()) {
    for (const [turnId, turn] of map.entries()) {
      if (turn.verdict?.id === verdictId) {
        map.set(turnId, { ...turn, verdict: { ...turn.verdict, saved } });
        changedChatIds.add(chatId);
      }
    }
  }
  changedChatIds.forEach(emitChat);
}

export function subscribeChatTurns(chatId: string, listener: (turns: ApiTurn[]) => void) {
  if (!chatListeners.has(chatId)) chatListeners.set(chatId, new Set());
  chatListeners.get(chatId)!.add(listener);
  listener(getChatTurns(chatId));
  return () => chatListeners.get(chatId)?.delete(listener);
}

export function subscribeChatRunning(chatId: string, listener: (running: boolean) => void) {
  if (!runningListeners.has(chatId)) runningListeners.set(chatId, new Set());
  runningListeners.get(chatId)!.add(listener);
  listener(isChatRunning(chatId));
  return () => runningListeners.get(chatId)?.delete(listener);
}

export function subscribeActiveTurn(chatId: string, listener: (turnId: string | null) => void) {
  if (!activeTurnListeners.has(chatId)) activeTurnListeners.set(chatId, new Set());
  activeTurnListeners.get(chatId)!.add(listener);
  listener(getActiveTurnId(chatId));
  return () => activeTurnListeners.get(chatId)?.delete(listener);
}

function isFullTurnPayload(data: unknown): data is ApiTurn {
  return (
    typeof data === "object" && data !== null && Array.isArray((data as ApiTurn).model_answers)
  );
}

export function runTurnInBackground(auth: Auth, chatId: string, pending: ApiTurn): Promise<void> {
  const existing = activeJobs.get(pending.id);
  if (existing) return existing.promise;

  deletedTurns.delete(pending.id);
  updateTurn(chatId, pending);

  const controller = new AbortController();
  const promise = streamTurn(
    auth,
    pending.id,
    (event, data) => {
      if (event === "turn_deleted") {
        removeTurn(chatId, pending.id);
        return;
      }
      const current = getChatTurnMap(chatId).get(pending.id);
      if (!current || deletedTurns.has(pending.id)) return;
      let next: ApiTurn;
      if (event === "turn_progress" || event === "turn_completed") {
        if (isFullTurnPayload(data)) {
          next = mergeTurnFromApi(current, data);
          if (event === "turn_completed") next = { ...next, status: data.status ?? next.status };
        } else if (event === "turn_completed") {
          const status = String((data as { status?: string }).status ?? "completed");
          next = { ...current, status };
        } else {
          return;
        }
      } else {
        next = applyStreamEvent(current, event, data as Record<string, unknown>);
      }
      updateTurn(chatId, next);
    },
    { signal: controller.signal, isTurnDeleted: isTurnDeletedLocally },
  )
    .then((result) => {
      if (result?.reason === "turn_deleted") {
        removeTurn(chatId, pending.id);
      }
    })
    .catch((error) => {
      if (isRequestCancelled(error) || deletedTurns.has(pending.id)) return;
      console.error("Turn stream failed:", error);
      throw error;
    })
    .finally(() => {
      activeJobs.delete(pending.id);
      emitChat(chatId);
    });

  activeJobs.set(pending.id, { chatId, controller, promise, stopping: false });
  emitChat(chatId);
  return promise;
}

export async function stopActiveTurn(auth: Auth, chatId: string): Promise<void> {
  const turnId = getActiveTurnId(chatId);
  if (!turnId) return;
  const job = activeJobs.get(turnId);
  if (!job || job.stopping) return;

  job.stopping = true;
  try {
    await api.chats.deleteTurn(auth, chatId, turnId);
    removeTurn(chatId, turnId);
  } finally {
    const currentJob = activeJobs.get(turnId);
    if (currentJob === job) {
      currentJob.stopping = false;
      emitChat(chatId);
    }
  }
}

export async function resumeRunningTurns(auth: Auth, chatId: string, turns: ApiTurn[]) {
  for (const turn of turns) {
    if (
      (turn.status === "pending" || turn.status === "running") &&
      !activeJobs.has(turn.id) &&
      !deletedTurns.has(turn.id)
    ) {
      updateTurn(chatId, turn);
      void runTurnInBackground(auth, chatId, turn).catch(() => undefined);
    }
  }
}

export function mergeWithCachedTurns(chatId: string, apiTurns: ApiTurn[]): ApiTurn[] {
  return mergeTurnLists(apiTurns, getChatTurns(chatId));
}
