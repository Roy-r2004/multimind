/** Live chat-title sync over the authenticated chat-events SSE stream. */

export type ChatTitleTarget = { id: string; title: string };

export type ChatTitleEvent = { chatId: string; title: string; updatedAt?: string };

export type ChatDeletedEvent = { chatId: string };

export type PendingTitles = Map<string, ChatTitleEvent>;

export type ChatCreatedEvent = {
  id: string;
  title: string;
  project_id: string | null;
  model_set_id: string | null;
  pinned_verdicts: [];
  active_referenced_chat: null;
  updated_at: string;
};

type Auth = { token: string; orgId?: string | null };

const INITIAL_BACKOFF_MS = 1000;
const MAX_BACKOFF_MS = 15000;

function defaultApiBase(): string {
  const env = (import.meta as { env?: { VITE_API_URL?: string } }).env;
  return (env?.VITE_API_URL ?? "/api/v1").replace(/\/$/, "");
}

function isOlder(candidate: string | undefined, current: string | undefined): boolean {
  return Boolean(candidate && current && candidate < current);
}

export function applyChatTitle<T extends ChatTitleTarget & { updatedAt?: string }>(
  chats: T[],
  event: ChatTitleEvent,
): T[] {
  let changed = false;
  const next = chats.map((chat) => {
    if (chat.id !== event.chatId) return chat;
    if (isOlder(event.updatedAt, chat.updatedAt)) return chat;
    if (chat.title === event.title && chat.updatedAt === (event.updatedAt ?? chat.updatedAt)) return chat;
    changed = true;
    return { ...chat, title: event.title, updatedAt: event.updatedAt ?? chat.updatedAt };
  });
  return changed ? next : chats;
}

/** Remember a title that arrived before the chat row. Newer stamps win. */
export function rememberPendingTitle(pending: PendingTitles, event: ChatTitleEvent): void {
  const current = pending.get(event.chatId);
  if (current && isOlder(event.updatedAt, current.updatedAt)) return;
  pending.set(event.chatId, event);
}

/** Insert a newly created chat by server timestamp. Existing ids stay put. */
export function applyChatCreated<T extends { id: string; updatedAt?: string; title: string }>(
  chats: T[],
  chat: T,
  pending?: PendingTitles,
): T[] {
  if (chats.some((item) => item.id === chat.id)) return chats;
  let next = chat;
  const earlier = pending?.get(chat.id);
  if (earlier && !isOlder(earlier.updatedAt, chat.updatedAt)) {
    next = { ...chat, title: earlier.title, updatedAt: earlier.updatedAt ?? chat.updatedAt };
    pending?.delete(chat.id);
  }
  const stamp = next.updatedAt ?? "";
  if (!stamp) return [next, ...chats];
  const index = chats.findIndex((item) => (item.updatedAt ?? "") < stamp);
  const at = index < 0 ? chats.length : index;
  return [...chats.slice(0, at), next, ...chats.slice(at)];
}

export function applyChatDeleted<T extends { id: string }>(chats: T[], chatId: string): T[] {
  if (!chats.some((chat) => chat.id === chatId)) return chats;
  return chats.filter((chat) => chat.id !== chatId);
}

/**
 * Rebuild sidebar order from the server list.
 * Keeps the local object when title, project, and model set already match.
 */
export function mergeChatHistory<T extends ChatTitleTarget & {
  projectId?: string | null;
  modelSetId?: string | null;
  updatedAt?: string;
}>(local: T[], remote: T[]): T[] {
  const localById = new Map(local.map((chat) => [chat.id, chat]));
  const next = remote.map((chat) => {
    const existing = localById.get(chat.id);
    if (!existing) return chat;
    if (isOlder(chat.updatedAt, existing.updatedAt)) {
      return existing;
    }
    if (
      existing.title === chat.title &&
      existing.projectId === chat.projectId &&
      existing.modelSetId === chat.modelSetId &&
      existing.updatedAt === chat.updatedAt
    ) {
      return existing;
    }
    return chat;
  });
  if (next.length === local.length && next.every((chat, index) => chat === local[index])) {
    return local;
  }
  return next;
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

export function parseChatCreatedEvent(data: string): ChatCreatedEvent | null {
  try {
    const parsed = JSON.parse(data) as {
      type?: unknown;
      chat_id?: unknown;
      title?: unknown;
      project_id?: unknown;
      model_set_id?: unknown;
      updated_at?: unknown;
    };
    if (parsed.type !== "chat.created") return null;
    if (typeof parsed.chat_id !== "string" || !parsed.chat_id) return null;
    if (typeof parsed.title !== "string" || !parsed.title.trim()) return null;
    if (typeof parsed.updated_at !== "string" || !parsed.updated_at) return null;
    const projectId = parsed.project_id;
    const modelSetId = parsed.model_set_id;
    if (projectId !== null && projectId !== undefined && typeof projectId !== "string") return null;
    if (modelSetId !== null && modelSetId !== undefined && typeof modelSetId !== "string") return null;
    return {
      id: parsed.chat_id,
      title: parsed.title,
      project_id: typeof projectId === "string" ? projectId : null,
      model_set_id: typeof modelSetId === "string" ? modelSetId : null,
      pinned_verdicts: [],
      active_referenced_chat: null,
      updated_at: parsed.updated_at,
    };
  } catch {
    return null;
  }
}

export function parseChatDeletedEvent(data: string): ChatDeletedEvent | null {
  try {
    const parsed = JSON.parse(data) as { type?: unknown; chat_id?: unknown };
    if (parsed.type !== "chat.deleted") return null;
    if (typeof parsed.chat_id !== "string" || !parsed.chat_id) return null;
    return { chatId: parsed.chat_id };
  } catch {
    return null;
  }
}

export function parseChatTitleEvent(data: string): ChatTitleEvent | null {
  try {
    const parsed = JSON.parse(data) as { chat_id?: unknown; title?: unknown; updated_at?: unknown };
    if (typeof parsed.chat_id !== "string" || typeof parsed.title !== "string") return null;
    if (!parsed.chat_id || !parsed.title.trim()) return null;
    const updatedAt = parsed.updated_at;
    if (updatedAt !== undefined && typeof updatedAt !== "string") return null;
    return {
      chatId: parsed.chat_id,
      title: parsed.title,
      updatedAt: typeof updatedAt === "string" && updatedAt ? updatedAt : undefined,
    };
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
  onCreated: (event: ChatCreatedEvent) => void,
  onDeleted: (event: ChatDeletedEvent) => void,
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
        if (frame.event === "chat.created") {
          const created = parseChatCreatedEvent(frame.data);
          if (created) onCreated(created);
          continue;
        }
        if (frame.event === "chat.deleted") {
          const deleted = parseChatDeletedEvent(frame.data);
          if (deleted) onDeleted(deleted);
          continue;
        }
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
  onCreated?: (event: ChatCreatedEvent) => void;
  onDeleted?: (event: ChatDeletedEvent) => void;
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
      await readTitleStream(
        response.body,
        options.onTitle,
        options.onCreated ?? (() => {}),
        options.onDeleted ?? (() => {}),
        options.signal,
      );
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
