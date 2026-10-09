/** Live chat-title sync over the authenticated chat-events SSE stream. */

export type ChatTitleTarget = { id: string; title: string };

export type ChatTitleEvent = { chatId: string; title: string };

type Auth = { token: string; orgId?: string | null };

const INITIAL_BACKOFF_MS = 1000;
const MAX_BACKOFF_MS = 15000;

function defaultApiBase(): string {
  const env = (import.meta as { env?: { VITE_API_URL?: string } }).env;
  return (env?.VITE_API_URL ?? "/api/v1").replace(/\/$/, "");
}

export function applyChatTitle<T extends ChatTitleTarget>(chats: T[], event: ChatTitleEvent): T[] {
  let changed = false;
  const next = chats.map((chat) => {
    if (chat.id !== event.chatId || chat.title === event.title) return chat;
    changed = true;
    return { ...chat, title: event.title };
  });
  return changed ? next : chats;
}

/** Replace titles in place so reconnect recovery does not reorder or reset chats. */
export function mergeChatTitles<T extends ChatTitleTarget>(chats: T[], remote: ChatTitleTarget[]): T[] {
  const titles = new Map(remote.map((chat) => [chat.id, chat.title]));
  let changed = false;
  const next = chats.map((chat) => {
    const title = titles.get(chat.id);
    if (title === undefined || title === chat.title) return chat;
    changed = true;
    return { ...chat, title };
  });
  return changed ? next : chats;
}

export function parseSseFrames(buffer: string): { frames: { event: string; data: string }[]; rest: string } {
  const parts = buffer.split("\n\n");
  const rest = parts.pop() ?? "";
  const frames: { event: string; data: string }[] = [];
  for (const part of parts) {
    if (!part.trim() || part.startsWith(":")) continue;
    let event = "message";
    const dataLines: string[] = [];
    for (const line of part.split("\n")) {
      if (line.startsWith(":")) continue;
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    }
    if (dataLines.length) frames.push({ event, data: dataLines.join("\n") });
  }
  return { frames, rest };
}

export function parseChatTitleEvent(data: string): ChatTitleEvent | null {
  try {
    const parsed = JSON.parse(data) as { chat_id?: unknown; title?: unknown };
    if (typeof parsed.chat_id !== "string" || typeof parsed.title !== "string") return null;
    if (!parsed.chat_id || !parsed.title.trim()) return null;
    return { chatId: parsed.chat_id, title: parsed.title };
  } catch {
    return null;
  }
}

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("aborted", "AbortError"));
      return;
    }
    const timer = setTimeout(resolve, ms);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        reject(new DOMException("aborted", "AbortError"));
      },
      { once: true },
    );
  });
}

async function readTitleStream(
  body: ReadableStream<Uint8Array>,
  onTitle: (event: ChatTitleEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (!signal.aborted) {
      const { done, value } = await reader.read();
      if (done) return;
      buffer += decoder.decode(value, { stream: true });
      const parsed = parseSseFrames(buffer);
      buffer = parsed.rest;
      for (const frame of parsed.frames) {
        if (frame.event !== "chat_title") continue;
        const event = parseChatTitleEvent(frame.data);
        if (event) onTitle(event);
      }
    }
  } finally {
    try {
      await reader.cancel();
    } catch {
      /* already closed */
    }
  }
}

export async function runChatTitleSync(options: {
  auth: Auth;
  signal: AbortSignal;
  onTitle: (event: ChatTitleEvent) => void;
  refreshTitles: () => Promise<void>;
  fetchImpl?: typeof fetch;
  sleepImpl?: (ms: number, signal: AbortSignal) => Promise<void>;
}): Promise<void> {
  const fetchImpl = options.fetchImpl ?? fetch;
  const sleepImpl = options.sleepImpl ?? sleep;
  let backoff = INITIAL_BACKOFF_MS;
  let sawConnection = false;

  while (!options.signal.aborted) {
    let connected = false;
    try {
      const headers: Record<string, string> = {
        Accept: "text/event-stream",
        Authorization: `Bearer ${options.auth.token}`,
      };
      if (options.auth.orgId) headers["X-Org-Id"] = options.auth.orgId;
      const response = await fetchImpl(`${defaultApiBase()}/chats/events/stream`, {
        headers,
        signal: options.signal,
      });
      if (!response.ok || !response.body) {
        throw new Error(`chat title stream failed: ${response.status}`);
      }
      connected = true;
      sawConnection = true;
      backoff = INITIAL_BACKOFF_MS;
      await readTitleStream(response.body, options.onTitle, options.signal);
    } catch (error) {
      if (options.signal.aborted || (error instanceof DOMException && error.name === "AbortError")) {
        return;
      }
    }

    if (options.signal.aborted) return;
    if (connected || sawConnection) {
      try {
        await options.refreshTitles();
      } catch {
        /* next reconnect retries the snapshot */
      }
    }
    try {
      await sleepImpl(backoff, options.signal);
    } catch {
      return;
    }
    backoff = Math.min(backoff * 2, MAX_BACKOFF_MS);
  }
}
