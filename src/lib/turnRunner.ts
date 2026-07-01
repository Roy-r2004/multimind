/** Keeps turn streaming alive across route changes. */

import { streamTurn } from "@/lib/api/stream";
import type { ApiTurn } from "@/lib/api/types";
import { applyStreamEvent, mergeTurnFromApi, mergeTurnLists } from "@/lib/turnState";

type Auth = { token: string; orgId?: string | null };

const turnsByChat = new Map<string, Map<string, ApiTurn>>();
const activeJobs = new Map<string, { chatId: string; promise: Promise<void> }>();
const chatListeners = new Map<string, Set<(turns: ApiTurn[]) => void>>();
const runningListeners = new Map<string, Set<(running: boolean) => void>>();

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

function emitChat(chatId: string) {
  const turns = getChatTurns(chatId);
  chatListeners.get(chatId)?.forEach((fn) => fn(turns));
  const running = isChatRunning(chatId);
  runningListeners.get(chatId)?.forEach((fn) => fn(running));
}

function updateTurn(chatId: string, turn: ApiTurn) {
  getChatTurnMap(chatId).set(turn.id, turn);
  emitChat(chatId);
}

export function seedChatTurns(chatId: string, turns: ApiTurn[]) {
  const map = getChatTurnMap(chatId);
  for (const turn of turns) {
    const existing = map.get(turn.id);
    map.set(turn.id, existing ? mergeTurnFromApi(existing, turn) : turn);
  }
  emitChat(chatId);
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

export function runTurnInBackground(auth: Auth, chatId: string, pending: ApiTurn): Promise<void> {
  const existing = activeJobs.get(pending.id);
  if (existing) return existing.promise;

  updateTurn(chatId, pending);

  const promise = streamTurn(auth, pending.id, (event, data) => {
    const current = getChatTurnMap(chatId).get(pending.id) ?? pending;
    let next: ApiTurn;
    if (event === "turn_progress" || event === "turn_completed") {
      next = mergeTurnFromApi(current, data as ApiTurn);
      if (event === "turn_completed") next = { ...next, status: (data as ApiTurn).status ?? next.status };
    } else {
      next = applyStreamEvent(current, event, data as Record<string, unknown>);
    }
    updateTurn(chatId, next);
  })
    .catch((error) => {
      console.error("Turn stream failed:", error);
      throw error;
    })
    .finally(() => {
      activeJobs.delete(pending.id);
      emitChat(chatId);
    });

  activeJobs.set(pending.id, { chatId, promise });
  emitChat(chatId);
  return promise;
}

export async function resumeRunningTurns(auth: Auth, chatId: string, turns: ApiTurn[]) {
  for (const turn of turns) {
    if ((turn.status === "pending" || turn.status === "running") && !activeJobs.has(turn.id)) {
      updateTurn(chatId, turn);
      void runTurnInBackground(auth, chatId, turn).catch(() => undefined);
    }
  }
}

export function mergeWithCachedTurns(chatId: string, apiTurns: ApiTurn[]): ApiTurn[] {
  return mergeTurnLists(apiTurns, getChatTurns(chatId));
}
