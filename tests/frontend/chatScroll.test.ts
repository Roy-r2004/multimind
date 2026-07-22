import assert from "node:assert/strict";
import test from "node:test";

import {
  CHAT_BOTTOM_THRESHOLD_PX,
  distanceFromChatBottom,
  isChatNearBottom,
  shouldShowScrollToLatest,
} from "../../src/lib/chatScroll.ts";

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
