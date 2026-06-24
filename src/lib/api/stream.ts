/** SSE stream client for turn orchestration. */

import { getApiBase } from "@/lib/api/client";

type Auth = { token: string; orgId?: string | null };

export type TurnStreamHandler = (event: string, data: unknown) => void;

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
          onEvent(eventType, JSON.parse(dataStr));
        } catch {
          onEvent(eventType, dataStr);
        }
      }
    }
  }
}
