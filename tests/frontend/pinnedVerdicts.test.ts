import assert from "node:assert/strict";
import test from "node:test";

import {
  buildPinnedVerdictMenuItems,
  isVerdictPinned,
  pinnedVerdictLabel,
  pinVerdictPath,
  verdictPinRequest,
} from "../../src/lib/pinnedVerdicts.ts";

const pins = [
  { verdictId: "A", turnId: "TA" },
  { verdictId: "C", turnId: "TC" },
];

test("pin and independent unpin endpoint paths include the verdict id", () => {
  assert.deepEqual(verdictPinRequest("chat-1", "A", "pin"), {
    path: "/chats/chat-1/pinned-verdicts/A",
    method: "PUT",
  });
  assert.deepEqual(verdictPinRequest("chat-1", "B", "unpin"), {
    path: "/chats/chat-1/pinned-verdicts/B",
    method: "DELETE",
  });
  assert.equal(pinVerdictPath("chat-1", "A"), "/chats/chat-1/pinned-verdicts/A");
});

test("multiple pinned membership is independent", () => {
  assert.equal(isVerdictPinned(pins, "A"), true);
  assert.equal(isVerdictPinned(pins, "B"), false);
  assert.equal(isVerdictPinned(pins, "C"), true);
});

test("zero, one, and multiple pin collections report membership correctly", () => {
  assert.equal(isVerdictPinned([], "A"), false);
  assert.equal(isVerdictPinned([pins[0]!], "A"), true);
  assert.equal(isVerdictPinned(pins, "C"), true);
});

test("navigation control data is empty for zero pins and includes every pin otherwise", () => {
  const turns = [{ id: "TA" }, { id: "TC" }] as never[];
  assert.deepEqual(buildPinnedVerdictMenuItems([], turns), []);
  assert.deepEqual(
    buildPinnedVerdictMenuItems([pins[0]!], turns).map((item) => item.verdictId),
    ["A"],
  );
  assert.deepEqual(
    buildPinnedVerdictMenuItems(pins, turns).map((item) => item.verdictId),
    ["A", "C"],
  );
});

test("pinned menu labels resolve visible turn positions with a safe fallback", () => {
  const turns = [{ id: "TA" }, { id: "TB" }, { id: "TC" }] as never[];
  assert.equal(pinnedVerdictLabel(pins[1]!, turns), "Verdict — Turn 3");
  assert.equal(
    pinnedVerdictLabel({ verdictId: "missing", turnId: "missing" }, turns),
    "Pinned verdict",
  );
});
