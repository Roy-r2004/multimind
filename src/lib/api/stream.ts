/** SSE stream client for turn orchestration. */

import { apiRequest, getApiBase } from "@/lib/api/client";
import type { ApiTurn } from "@/lib/api/types";

type Auth = { token: string; orgId?: string | null };

export type TurnStreamHandler = (event: string, data: unknown) => void;

function streamErrorMessage(event: string, data: unknown): string {
  if (typeof data === "object" && data) {
    if (event === "error" && "message" in data) return String((data as { message: string }).message);
    if (event === "turn_failed" && "error" in data) return String((data as { error: string }).error);
  }
  if (typeof data === "string" && data.trim()) return data;
  return "Turn failed — the council may still be working. Please wait a moment.";
}

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function pollTurnUntilComplete(
  auth: Auth,
  turnId: string,
  onEvent: TurnStreamHandler,
  options?: { maxAttempts?: number; intervalMs?: number; emitProgress?: boolean },
): Promise<ApiTurn> {
  const maxAttempts = options?.maxAttempts ?? 120;
  const intervalMs = options?.intervalMs ?? 2000;
  const emitProgress = options?.emitProgress ?? true;

  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    const turn = await apiRequest<ApiTurn>(`/chats/turns/${turnId}`, {
      token: auth.token,
      orgId: auth.orgId,
    });

    if (emitProgress) onEvent("turn_progress", turn);

    if (turn.status === "completed" || turn.status === "partial") {
      onEvent("turn_completed", turn);
      return turn;
    }
    if (turn.status === "failed") {
      const message = turn.model_answers.find((a) => a.error_message)?.error_message ?? "Turn failed";
      throw new Error(message);
    }

    await sleep(intervalMs);
  }

  throw new Error(
    "This is taking longer than expected. Refresh the page — your turn may still complete in the background.",
  );
}

export async function streamTurn(
  auth: Auth,
  turnId: string,
  onEvent: TurnStreamHandler,
): Promise<void> {
  const headers: Record<string, string> = {
    Accept: "text/event-stream",
    Authorization: `Bearer ${auth.token}`,
  };
  if (auth.orgId) headers["X-Org-Id"] = auth.orgId;

  let completed = false;
  let streamError: Error | null = null;

  const dispatch = (event: string, data: unknown) => {
    if (event === "ping") return;
    if (event === "turn_completed") {
      completed = true;
      onEvent(event, data);
      return;
    }
    if (event === "error" || event === "turn_failed") {
      streamError = new Error(streamErrorMessage(event, data));
      return;
    }
    onEvent(event, data);
  };

  try {
    const res = await fetch(`${getApiBase()}/chats/turns/${turnId}/stream`, { headers });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(text || res.statusText);
    }

    const reader = res.body?.getReader();
    if (!reader) throw new Error("No response body");

    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
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
        if (dataStr) {
          try {
            dispatch(eventType, JSON.parse(dataStr));
          } catch {
            dispatch(eventType, dataStr);
          }
        }
      }
    }
  } catch (error) {
    if (!completed) {
      try {
        await pollTurnUntilComplete(auth, turnId, onEvent, { emitProgress: false });
        return;
      } catch {
        // fall through to original error
      }
    }
    throw error instanceof Error ? error : new Error(String(error));
  }

  if (completed) return;

  const pollOpts = { emitProgress: false };

  if (streamError) {
    try {
      await pollTurnUntilComplete(auth, turnId, onEvent, pollOpts);
      return;
    } catch {
      throw streamError;
    }
  }

  try {
    await pollTurnUntilComplete(auth, turnId, onEvent, pollOpts);
  } catch (error) {
    throw error instanceof Error ? error : new Error(String(error));
  }
}
