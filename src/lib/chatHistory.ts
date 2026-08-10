import type { Chat } from "@/lib/mock";
import type { ApiChat } from "@/lib/api/types";

export const RECENT_CHAT_LIMIT = 20;

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
    projectId: c.project_id,
    modelSetId: c.model_set_id ?? null,
    pinnedVerdictId: c.pinned_verdict_id ?? null,
    pinnedTurnId: c.pinned_turn_id ?? null,
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
    projectId: existing?.projectId ?? null,
    modelSetId: data.modelSetId ?? existing?.modelSetId ?? null,
    pinnedVerdictId: existing?.pinnedVerdictId ?? null,
    pinnedTurnId: existing?.pinnedTurnId ?? null,
  };
}
