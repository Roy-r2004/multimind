import assert from "node:assert/strict";
import test from "node:test";

import {
  CHAT_BOTTOM_THRESHOLD_PX,
  captureChatScrollSnapshotFromLayout,
  chatScrollSessionOnEnter,
  chatScrollSessionOnSend,
  chatScrollSessionOnTurnsReady,
  distanceFromChatBottom,
  findVerdictSynthesisElement,
  getChatScrollSnapshot,
  isChatNearBottom,
  isLiveChatThread,
  nextScrollTopForRestore,
  persistChatScrollSnapshot,
  planChatScrollEnter,
  rememberChatScrollMemory,
  resetChatScrollSnapshots,
  setChatScrollSnapshot,
  shouldPinToBottomForPlan,
  shouldShowScrollToLatest,
  type ChatScrollSnapshot,
} from "../../src/lib/chatScroll.ts";

function nonBottomSnapshot(overrides: Partial<ChatScrollSnapshot> = {}): ChatScrollSnapshot {
  return {
    nearBottom: false,
    turnId: "turn-23",
    offsetPx: 120,
    scrollTop: 5821,
    ...overrides,
  };
}

test("distanceFromChatBottom calculates remaining scrollable distance", () => {
  assert.equal(
    distanceFromChatBottom({ scrollTop: 700, scrollHeight: 1200, clientHeight: 400 }),
    100,
  );
});

test("near-bottom detection allows the configured threshold", () => {
  assert.equal(
    isChatNearBottom({
      scrollTop: 1200 - 400 - CHAT_BOTTOM_THRESHOLD_PX,
      scrollHeight: 1200,
      clientHeight: 400,
    }),
    true,
  );
});

test("near-bottom detection rejects positions beyond the threshold", () => {
  assert.equal(
    isChatNearBottom({
      scrollTop: 1200 - 400 - CHAT_BOTTOM_THRESHOLD_PX - 1,
      scrollHeight: 1200,
      clientHeight: 400,
    }),
    false,
  );
});

test("scroll-to-latest button is hidden near the bottom", () => {
  assert.equal(
    shouldShowScrollToLatest({ scrollTop: 695, scrollHeight: 1200, clientHeight: 400 }),
    false,
  );
});

test("scroll-to-latest button is visible when scrolled upward", () => {
  assert.equal(
    shouldShowScrollToLatest({ scrollTop: 500, scrollHeight: 1200, clientHeight: 400 }),
    true,
  );
});

test("selected pin navigation prefers the selected verdict id", () => {
  const selected = { id: "verdict-B" } as HTMLElement;
  const originalDocument = globalThis.document;
  globalThis.document = {
    getElementById: (id: string) => (id === "verdict-B" ? selected : null),
  } as Document;
  try {
    assert.equal(findVerdictSynthesisElement("B", "TB"), selected);
  } finally {
    globalThis.document = originalDocument;
  }
});

test("non-bottom snapshot stores the first visible turn id and offset", () => {
  const snapshot = captureChatScrollSnapshotFromLayout(
    { scrollTop: 2400, scrollHeight: 8000, clientHeight: 500 },
    80,
    [
      { turnId: "22", top: -40, height: 40 },
      { turnId: "23", top: 200, height: 300 },
      { turnId: "24", top: 520, height: 300 },
    ],
  );
  assert.equal(snapshot.nearBottom, false);
  assert.equal(snapshot.turnId, "23");
  assert.equal(snapshot.offsetPx, 120);
  assert.equal(snapshot.scrollTop, 2400);
});

test("near-bottom snapshot is treated as latest on enter", () => {
  const snapshot = captureChatScrollSnapshotFromLayout(
    {
      scrollTop: 8000 - 500 - CHAT_BOTTOM_THRESHOLD_PX,
      scrollHeight: 8000,
      clientHeight: 500,
    },
    80,
    [{ turnId: "100", top: 200, height: 400 }],
  );
  assert.equal(snapshot.nearBottom, true);
  const plan = planChatScrollEnter(snapshot, null);
  assert.equal(plan.action, "pin-latest");
  assert.equal(shouldPinToBottomForPlan(plan), true);
});

test("missing snapshot preserves first-open latest behavior", () => {
  const plan = planChatScrollEnter(undefined, null);
  assert.equal(plan.action, "pin-latest");
  assert.equal(shouldPinToBottomForPlan(plan), true);
});

test("restore of an old turn preserves its viewport offset", () => {
  const snapshot = nonBottomSnapshot({ offsetPx: 120, scrollTop: 2400 });
  assert.equal(nextScrollTopForRestore(0, snapshot, 400), 280);
  assert.equal(nextScrollTopForRestore(280, snapshot, 120), 280);
});

test("missing or deleted turn anchor falls back to saved scrollTop", () => {
  const snapshot = nonBottomSnapshot({ turnId: "deleted", scrollTop: 5821, offsetPx: 90 });
  assert.equal(nextScrollTopForRestore(0, snapshot, null), 5821);
  assert.equal(nextScrollTopForRestore(12, nonBottomSnapshot({ nearBottom: true }), null), 12);
});

test("chat A and chat B snapshots remain independent", () => {
  resetChatScrollSnapshots();
  setChatScrollSnapshot("chat-a", nonBottomSnapshot({ turnId: "23", offsetPx: 120 }));
  setChatScrollSnapshot("chat-b", nonBottomSnapshot({ turnId: "8", offsetPx: 40, scrollTop: 900 }));
  assert.equal(getChatScrollSnapshot("chat-a")?.turnId, "23");
  assert.equal(getChatScrollSnapshot("chat-a")?.offsetPx, 120);
  assert.equal(getChatScrollSnapshot("chat-b")?.turnId, "8");
  assert.equal(getChatScrollSnapshot("chat-b")?.offsetPx, 40);
  resetChatScrollSnapshots();
  assert.equal(getChatScrollSnapshot("chat-a"), undefined);
});

test("non-bottom restoration leaves follow-latest pin off", () => {
  const plan = planChatScrollEnter(nonBottomSnapshot(), null);
  assert.equal(plan.action, "restore");
  assert.equal(shouldPinToBottomForPlan(plan), false);
});

test("sending a new message re-enables pin-to-latest behavior", () => {
  const reading = planChatScrollEnter(nonBottomSnapshot(), null);
  assert.equal(shouldPinToBottomForPlan(reading), false);
  const afterSend = planChatScrollEnter(undefined, null);
  assert.equal(afterSend.action, "pin-latest");
  assert.equal(shouldPinToBottomForPlan(afterSend), true);
});

test("explicit turnId navigation takes precedence over a saved snapshot", () => {
  const plan = planChatScrollEnter(nonBottomSnapshot({ turnId: "23" }), "turn-explicit");
  assert.deepEqual(plan, { action: "explicit-turn", turnId: "turn-explicit" });
  assert.equal(shouldPinToBottomForPlan(plan), false);
});

test("detached thread metrics collapse to near-bottom and must not be persisted", () => {
  assert.equal(isChatNearBottom({ scrollTop: 0, scrollHeight: 0, clientHeight: 0 }), true);
  assert.equal(isLiveChatThread({ isConnected: false, clientHeight: 0, scrollHeight: 0 }), false);
  assert.equal(
    isLiveChatThread({ isConnected: true, clientHeight: 400, scrollHeight: 8000 }),
    true,
  );
});

test("unmount persist uses last live memory instead of collapsed detached layout", () => {
  resetChatScrollSnapshots();
  rememberChatScrollMemory("chat-a", {
    scrollTop: 2400,
    scrollHeight: 8000,
    clientHeight: 500,
    turnId: "23",
    offsetPx: 120,
  });
  const collapsed = {
    isConnected: false,
    clientHeight: 0,
    scrollHeight: 0,
    scrollTop: 0,
    getBoundingClientRect: () => ({ top: 0, left: 0, width: 0, height: 0 }),
    querySelectorAll: () => [],
  } as unknown as HTMLElement;
  const snapshot = persistChatScrollSnapshot("chat-a", collapsed);
  assert.equal(snapshot?.nearBottom, false);
  assert.equal(snapshot?.turnId, "23");
  assert.equal(snapshot?.scrollTop, 2400);
  assert.equal(getChatScrollSnapshot("chat-a")?.nearBottom, false);
  resetChatScrollSnapshots();
});

test("non-bottom remount stays pending until turns appear, then restores without latest", () => {
  resetChatScrollSnapshots();
  const snapshot = nonBottomSnapshot();
  let session = chatScrollSessionOnEnter(snapshot, null);
  assert.equal(session.pendingRestore, true);
  assert.equal(session.restored, false);
  assert.equal(session.pin, false);
  assert.equal(session.latestCalls, 0);

  session = chatScrollSessionOnTurnsReady(session);
  assert.equal(session.pendingRestore, false);
  assert.equal(session.restored, true);
  assert.equal(session.pin, false);
  assert.equal(session.latestCalls, 0);
  resetChatScrollSnapshots();
});

test("no snapshot on remount calls latest", () => {
  const session = chatScrollSessionOnEnter(undefined, null);
  assert.equal(session.pin, true);
  assert.equal(session.latestCalls, 1);
  assert.equal(session.pendingRestore, false);
});

test("near-bottom snapshot on remount calls latest", () => {
  const session = chatScrollSessionOnEnter(nonBottomSnapshot({ nearBottom: true }), null);
  assert.equal(session.pin, true);
  assert.equal(session.latestCalls, 1);
});

test("deleted anchor restore uses fallback scrollTop", () => {
  assert.equal(
    nextScrollTopForRestore(0, nonBottomSnapshot({ turnId: "gone", scrollTop: 3333 }), null),
    3333,
  );
});

test("send after restore re-enables pin and latest", () => {
  let session = chatScrollSessionOnEnter(nonBottomSnapshot(), null);
  session = chatScrollSessionOnTurnsReady(session);
  assert.equal(session.pin, false);
  session = chatScrollSessionOnSend(session);
  assert.equal(session.pin, true);
  assert.equal(session.latestCalls, 1);
});
