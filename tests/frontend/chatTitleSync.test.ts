import assert from "node:assert/strict";
import test from "node:test";

import {
  applyChatTitle,
  mergeChatTitles,
  parseChatTitleEvent,
  parseSseFrames,
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

test("parseSseFrames reads chat_title and ignores heartbeats", () => {
  const { frames, rest } = parseSseFrames(
    ": heartbeat\n\n" +
      'event: chat_title\ndata: {"chat_id":"a","title":"Next"}\n\n' +
      "event: chat_title\ndata: {",
  );
  assert.equal(rest, "event: chat_title\ndata: {");
  assert.equal(frames.length, 1);
  assert.deepEqual(parseChatTitleEvent(frames[0].data), { chatId: "a", title: "Next" });
  assert.equal(parseChatTitleEvent('{"chat_id":"a"}'), null);
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
