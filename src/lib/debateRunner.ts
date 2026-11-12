/** Keeps Debate Mode's session fetch + SSE streaming alive independent of
 * which component/route currently has the panel open — mirrors
 * turnRunner.ts's module-level store for Council turns. The backend job
 * itself is already independent of any one HTTP/SSE connection (Phase 4);
 * this module is only about the frontend not losing track of it.
 */

import { api } from "@/lib/api";
import { getApiBase, isAbortError, isRequestCancelled } from "@/lib/api/client";
import type { ApiDebateSession } from "@/lib/api/types";
import { ApiClientError } from "@/lib/api/types";
import { applyDebateEvent, isDebateStatusActive, mergeDebateSnapshot } from "@/lib/debateState";

type Auth = { token: string; orgId?: string | null };

const sessionsByTurn = new Map<string, ApiDebateSession | null>();
const listenersByTurn = new Map<string, Set<(session: ApiDebateSession | null) => void>>();
const activeStreams = new Map<string, AbortController>();

function emit(turnId: string) {
  const session = sessionsByTurn.get(turnId) ?? null;
  listenersByTurn.get(turnId)?.forEach((fn) => fn(session));
}

function setSession(turnId: string, session: ApiDebateSession | null) {
  sessionsByTurn.set(turnId, session);
  emit(turnId);
}

export function getDebateSession(turnId: string): ApiDebateSession | null {
  return sessionsByTurn.get(turnId) ?? null;
}

export function subscribeDebate(
  turnId: string,
  listener: (session: ApiDebateSession | null) => void,
): () => void {
  if (!listenersByTurn.has(turnId)) listenersByTurn.set(turnId, new Set());
  listenersByTurn.get(turnId)!.add(listener);
  listener(getDebateSession(turnId));
  return () => listenersByTurn.get(turnId)?.delete(listener);
}

function applyAndStore(turnId: string, event: string, data: Record<string, unknown>) {
  const current = sessionsByTurn.get(turnId);
  if (!current) return;
  setSession(turnId, applyDebateEvent(current, event, data));
}

async function streamDebateSession(auth: Auth, turnId: string, sessionId: string) {
  if (activeStreams.has(sessionId)) return;
  const controller = new AbortController();
  activeStreams.set(sessionId, controller);

  const headers: Record<string, string> = {
    Accept: "text/event-stream",
    Authorization: `Bearer ${auth.token}`,
  };
  if (auth.orgId) headers["X-Org-Id"] = auth.orgId;

  const refetchSnapshot = async () => {
    try {
      const remote = await api.debate.getById(auth, sessionId);
      const current = sessionsByTurn.get(turnId);
      setSession(turnId, mergeDebateSnapshot(current ?? null, remote));
    } catch {
      // Best-effort reconciliation; the last applied SSE state stands.
    }
  };

  try {
    const res = await fetch(`${getApiBase()}/debate-sessions/${sessionId}/stream`, {
      headers,
      signal: controller.signal,
    });
    if (!res.ok || !res.body) throw new Error(`stream failed: ${res.status}`);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let terminal = false;

    while (!terminal) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() ?? "";

      for (const part of parts) {
        if (!part.trim()) continue;
        let eventType = "message";
        let dataStr = "";
        for (const line of part.split("\n")) {
          if (line.startsWith("event:")) eventType = line.slice(6).trim();
          if (line.startsWith("data:")) dataStr = line.slice(5).trim();
        }
        if (!dataStr) continue; // heartbeat/comment frame
        let parsed: Record<string, unknown> = {};
        try {
          const envelope = JSON.parse(dataStr) as { data?: Record<string, unknown> };
          parsed = envelope.data ?? {};
        } catch {
          continue;
        }
        applyAndStore(turnId, eventType, parsed);
        if (
          eventType === "debate_completed" ||
          eventType === "debate_partial" ||
          eventType === "debate_failed" ||
          eventType === "debate_cancelled"
        ) {
          terminal = true;
          break;
        }
      }
    }
  } catch (error) {
    if (!isAbortError(error) && !isRequestCancelled(error)) {
      // Live streaming degraded (e.g. a dead Redis); the session is not
      // lost — fall through to the authoritative snapshot refetch below.
    }
  } finally {
    activeStreams.delete(sessionId);
    await refetchSnapshot();
  }
}

function maybeResumeStream(auth: Auth, turnId: string, session: ApiDebateSession) {
  if (isDebateStatusActive(session.status)) {
    void streamDebateSession(auth, turnId, session.id);
  }
}

/** Fetches the durable snapshot for a turn's latest debate, if any — the
 * authoritative source for refresh/reopen/new-tab/navigate-back. Resolves
 * to `null` (never throws) when no debate has been started for this turn. */
export async function loadDebateForTurn(
  auth: Auth,
  turnId: string,
): Promise<ApiDebateSession | null> {
  try {
    const remote = await api.debate.getLatestForTurn(auth, turnId);
    const current = sessionsByTurn.get(turnId) ?? null;
    const merged = mergeDebateSnapshot(current, remote);
    setSession(turnId, merged);
    maybeResumeStream(auth, turnId, merged);
    return merged;
  } catch (error) {
    if (error instanceof ApiClientError && error.status === 404) {
      setSession(turnId, null);
      return null;
    }
    throw error;
  }
}

/** Starts a debate, or attaches to the one already active for this turn.
 * Safe to call repeatedly from a click handler — the backend is
 * idempotent, and this also guards against opening a second SSE stream
 * for the same session locally. */
export async function startDebate(auth: Auth, turnId: string): Promise<ApiDebateSession> {
  const { session, freshly_started } = await api.debate.start(auth, turnId);
  setSession(turnId, session);
  if (freshly_started || isDebateStatusActive(session.status)) {
    maybeResumeStream(auth, turnId, session);
  }
  return session;
}

export async function cancelDebate(
  auth: Auth,
  turnId: string,
  sessionId: string,
): Promise<ApiDebateSession> {
  const updated = await api.debate.cancel(auth, sessionId);
  setSession(turnId, updated);
  return updated;
}

export function isStreamingDebate(sessionId: string): boolean {
  return activeStreams.has(sessionId);
}
