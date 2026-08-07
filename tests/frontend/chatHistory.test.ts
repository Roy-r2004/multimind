import assert from "node:assert/strict";
import test from "node:test";

import {
  RECENT_CHAT_LIMIT,
  chatFromTurnActivity,
  filterChatsByTitle,
  recentChats,
  shouldShowSeeAll,
  upsertChatToTop,
} from "../../src/lib/chatHistory.ts";
import type { Chat } from "../../src/lib/mock.ts";

function chat(id: string, title = id): Chat {
  return { id, title, updated: "1m ago", projectId: null };
}

test("upsertChatToTop prepends a new chat id", () => {
  const next = upsertChatToTop([chat("a"), chat("b")], chat("c", "C"));
  assert.deepEqual(
    next.map((item) => item.id),
    ["c", "a", "b"],
  );
});

test("upsertChatToTop updates existing id and moves it to top without duplicating", () => {
  const next = upsertChatToTop(
    [chat("a", "A"), chat("b", "B"), chat("c", "C")],
    chat("b", "B-updated"),
  );
  assert.deepEqual(
    next.map((item) => [item.id, item.title]),
    [
      ["b", "B-updated"],
      ["a", "A"],
      ["c", "C"],
    ],
  );
});

test("recentChats shows all when at or under limit", () => {
  const list = Array.from({ length: 5 }, (_, i) => chat(`c${i}`));
  assert.equal(recentChats(list).length, 5);
  assert.equal(shouldShowSeeAll(list), false);
});

test("recentChats caps at RECENT_CHAT_LIMIT and enables See all", () => {
  const list = Array.from({ length: RECENT_CHAT_LIMIT + 5 }, (_, i) => chat(`c${i}`));
  const recent = recentChats(list);
  assert.equal(recent.length, RECENT_CHAT_LIMIT);
  assert.equal(recent[0]?.id, "c0");
  assert.equal(shouldShowSeeAll(list), true);
});

test("filterChatsByTitle is case-insensitive and trims", () => {
  const list = [chat("1", "Hello World"), chat("2", "rehab notes"), chat("3", "Other")];
  assert.deepEqual(
    filterChatsByTitle(list, "  HELLO ").map((item) => item.id),
    ["1"],
  );
  assert.deepEqual(
    filterChatsByTitle(list, "rehab").map((item) => item.id),
    ["2"],
  );
  assert.equal(filterChatsByTitle(list, "   ").length, 3);
});

test("chatFromTurnActivity prefers authoritative title and preserves project", () => {
  const existing = {
    ...chat("chat-1", "New chat"),
    projectId: "proj-1",
  };
  const next = chatFromTurnActivity(existing, {
    chatId: "chat-1",
    title: "What are the best options?",
    updatedAt: "2026-08-07T10:05:00Z",
  });
  assert.equal(next.title, "What are the best options?");
  assert.equal(next.projectId, "proj-1");
  assert.equal(next.id, "chat-1");
});
