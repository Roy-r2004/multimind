import assert from "node:assert/strict";
import test from "node:test";

import {
  applyChatCreated,
  applyChatDeleted,
  applyChatTitle,
  mergeChatHistory,
  mergeChatTitles,
  parseChatCreatedEvent,
  parseChatTitleEvent,
  parseSseFrames,
  rememberPendingTitle,
  runChatTitleSync,
} from "../../src/lib/chatTitleSync.ts";

const chats = [
  { id: "a", title: "Alpha", draft: "keep" },
  { id: "b", title: "Beta", draft: "also" },
];

test("applyChatTitle updates one title and keeps order and other fields", () => {
  const next = applyChatTitle(chats, { chatId: "b", title: "Beta renamed" });
  assert.deepEqual(
    next.map((chat) => chat.id),
    ["a", "b"],
  );
  assert.equal(next[0], chats[0]);
  assert.equal(next[1].title, "Beta renamed");
  assert.equal(next[1].draft, "also");
  assert.equal(applyChatTitle(chats, { chatId: "missing", title: "Nope" }), chats);
});

test("mergeChatTitles refreshes titles in place", () => {
  const next = mergeChatTitles(chats, [
    { id: "b", title: "Beta live" },
    { id: "a", title: "Alpha" },
  ]);
  assert.equal(next[0], chats[0]);
  assert.equal(next[1].title, "Beta live");
  assert.equal(next[1].draft, "also");
});

test("generated title is not overwritten by an older New chat snapshot", () => {
  const pending = new Map();
  rememberPendingTitle(pending, {
    chatId: "c",
    title: "Hello council",
    updatedAt: "2026-10-12T08:01:00+00:00",
  });
  const created = applyChatCreated(
    [],
    { id: "c", title: "New chat", updatedAt: "2026-10-12T08:00:00+00:00" },
    pending,
  );
  assert.equal(created[0].title, "Hello council");
  const stale = applyChatTitle(created, {
    chatId: "c",
    title: "New chat",
    updatedAt: "2026-10-12T08:00:00+00:00",
  });
  assert.equal(stale[0].title, "Hello council");
  const removed = applyChatDeleted(stale, "c");
  assert.deepEqual(removed, []);
  const ordered = applyChatCreated(
    [{ id: "old", title: "Old", updatedAt: "2026-10-12T07:00:00+00:00" }],
    { id: "new", title: "New chat", updatedAt: "2026-10-12T09:00:00+00:00" },
  );
  assert.deepEqual(
    ordered.map((chat) => chat.id),
    ["new", "old"],
  );
  assert.equal(applyChatCreated(ordered, ordered[0]), ordered);
});

test("applyChatCreated prepends once and leaves an existing row untouched", () => {
  const created = { id: "c", title: "Gamma", draft: "remote" };
  const next = applyChatCreated(chats, created);
  assert.deepEqual(
    next.map((chat) => chat.id),
    ["c", "a", "b"],
  );
  assert.equal(next[1], chats[0]);
  assert.equal(applyChatCreated(next, created), next);
});

test("mergeChatHistory inserts missed chats in server order and keeps matching rows", () => {
  const remote = [
    { id: "c", title: "Gamma" },
    { id: "a", title: "Alpha" },
  ];
  const next = mergeChatHistory(chats, remote);
  assert.equal(next[1], chats[0]);
  assert.equal(next[0].id, "c");
  assert.equal(mergeChatHistory(next, remote), next);
  const withTitle = [{ id: "a", title: "Hello council", updatedAt: "2026-10-12T08:02:00+00:00" }];
  const staleSnapshot = mergeChatHistory(withTitle, [
    { id: "a", title: "New chat", updatedAt: "2026-10-12T08:00:00+00:00" },
  ]);
  assert.equal(staleSnapshot[0].title, "Hello council");
  const afterDelete = mergeChatHistory(withTitle, []);
  assert.deepEqual(afterDelete, []);
});

test("parseSseFrames reads chat_title and ignores heartbeats", () => {
  const { frames, rest } = parseSseFrames(
    ": heartbeat\n\n" +
      'event: chat_title\ndata: {"chat_id":"a","title":"Next"}\n\n' +
      "event: chat_title\ndata: {",
  );
  assert.equal(rest, "event: chat_title\ndata: {");
  assert.equal(frames.length, 1);
  assert.deepEqual(parseChatTitleEvent(frames[0].data), {
    chatId: "a",
    title: "Next",
    updatedAt: undefined,
  });
  assert.equal(parseChatTitleEvent('{"chat_id":"a"}'), null);
  const created = parseChatCreatedEvent(
    '{"type":"chat.created","chat_id":"c","title":"Gamma","project_id":null,"model_set_id":"set","updated_at":"2026-10-12T08:00:00+00:00"}',
  );
  assert.equal(created?.id, "c");
  assert.equal(created?.model_set_id, "set");
  assert.equal(parseChatCreatedEvent('{"chat_id":"c","title":"Gamma"}'), null);
});

test("reconnect refreshes titles once and abort closes the loop", async () => {
  const encoder = new TextEncoder();
  let fetches = 0;
  const fetchImpl = async () => {
    fetches += 1;
    if (fetches === 2) {
      controller.abort();
      throw new DOMException("aborted", "AbortError");
    }
    const chunks = [
      encoder.encode('event: chat_title\ndata: {"chat_id":"b","title":"Remote"}\n\n'),
    ];
    let index = 0;
    const body = new ReadableStream({
      pull(controller) {
        if (index >= chunks.length) {
          controller.close();
          return;
        }
        controller.enqueue(chunks[index]);
        index += 1;
      },
    });
    return new Response(body, { status: 200 });
  };

  const titles: string[] = [];
  let refreshes = 0;
  const controller = new AbortController();
  const done = runChatTitleSync({
    auth: { token: "t", orgId: "org" },
    signal: controller.signal,
    onTitle: (event) => titles.push(event.title),
    refreshTitles: async () => {
      refreshes += 1;
    },
    fetchImpl: fetchImpl as typeof fetch,
    sleepImpl: async () => {},
  });
  await done;
  assert.deepEqual(titles, ["Remote"]);
  assert.equal(refreshes, 1);
  assert.equal(fetches, 2);
});
