import type { Chat } from "@/lib/mock";
import type { ApiChat } from "@/lib/api/types";

export const RECENT_CHAT_LIMIT = 20;

/** Page size for the paginated "See all" chat-history fetch (GET /chats/history). */
export const HISTORY_PAGE_SIZE = 30;

/** Debounce delay before a "See all" search keystroke triggers a server search. */
export const HISTORY_SEARCH_DEBOUNCE_MS = 300;

/** Upsert by id and move the chat to the front. Never duplicates an id. */
export function upsertChatToTop(chats: Chat[], next: Chat): Chat[] {
  const without = chats.filter((item) => item.id !== next.id);
  return [next, ...without];
}

export function recentChats(chats: Chat[], limit = RECENT_CHAT_LIMIT): Chat[] {
  return chats.slice(0, Math.max(0, limit));
}

export function shouldShowSeeAll(chats: Chat[], limit = RECENT_CHAT_LIMIT): boolean {
  return chats.length > limit;
}

export function filterChatsByTitle(chats: Chat[], query: string): Chat[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return chats;
  return chats.filter((chat) => chat.title.toLowerCase().includes(needle));
}

/**
 * Append a newly-fetched history page to what's already loaded, dropping any
 * incoming row whose id is already present. Pagination is keyset-based on
 * (updated_at, id), so a chat that gets a new turn between page fetches can
 * jump to the front of a later page's results — this keeps that from
 * producing a duplicate row instead of silently losing one.
 */
export function dedupeAppendChats(existing: Chat[], incoming: Chat[]): Chat[] {
  const seen = new Set(existing.map((item) => item.id));
  const deduped = incoming.filter((item) => !seen.has(item.id));
  return [...existing, ...deduped];
}

/**
 * Reconcile a separately-paginated chat-history page against the global chat
 * store: patch in authoritative metadata (title/pins/timestamps — kept live
 * by rename and the chat-title SSE stream) and drop rows the store confirms
 * were deleted. Guarded by `storeReady` so a store that hasn't finished its
 * own load yet never wipes out already-loaded history rows.
 */
export function reconcileHistoryWithStore(
  items: Chat[],
  storeChats: Chat[],
  storeReady: boolean,
): Chat[] {
  if (!storeReady) return items;
  const byId = new Map(storeChats.map((chat) => [chat.id, chat]));
  const next: Chat[] = [];
  for (const item of items) {
    const authoritative = byId.get(item.id);
    if (authoritative) next.push(authoritative);
  }
  return next;
}

export function formatRelativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `${mins || 1}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

export function mapApiChat(c: ApiChat): Chat {
  return {
    id: c.id,
    title: c.title,
    updated: formatRelativeTime(c.updated_at),
    updatedAt: c.updated_at,
    projectId: c.project_id,
    modelSetId: c.model_set_id ?? null,
    pinnedVerdicts: [
      ...c.pinned_verdicts.map((pin) => ({
        id: pin.id,
        pinType: pin.pin_type ?? "verdict",
        selectedText: pin.selected_text,
        selectedHtml: pin.selected_html,
        selectionLocator: pin.selection_locator,
        verdictId: pin.verdict_id,
        turnId: pin.turn_id,
      })),
      ...(c.pinned_answers ?? []).map((pin) => ({
        id: pin.id,
        pinType: pin.pin_type ?? "answer",
        selectedText: pin.selected_text,
        selectedHtml: pin.selected_html,
        selectionLocator: pin.selection_locator,
        verdictId: "",
        answerId: pin.answer_id,
        turnId: pin.turn_id,
      })),
    ],
    activeReferencedChat: c.active_referenced_chat ?? null,
  };
}

/** Build a Chat row from turn activity metadata (authoritative backend fields). */
export function chatFromTurnActivity(
  existing: Chat | undefined,
  data: {
    chatId: string;
    title?: string | null;
    updatedAt?: string | null;
    modelSetId?: string | null;
  },
): Chat {
  const title = (data.title?.trim() || existing?.title || "New chat").slice(0, 512);
  const updatedAt = data.updatedAt ?? new Date().toISOString();
  return {
    id: data.chatId,
    title,
    updated: formatRelativeTime(updatedAt),
    updatedAt,
    projectId: existing?.projectId ?? null,
    modelSetId: data.modelSetId ?? existing?.modelSetId ?? null,
    pinnedVerdicts: existing?.pinnedVerdicts ?? [],
    activeReferencedChat: existing?.activeReferencedChat ?? null,
  };
}
