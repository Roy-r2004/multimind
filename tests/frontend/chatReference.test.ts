import assert from "node:assert/strict";
import test from "node:test";

import {
  createTurnReferenceFields,
  shouldClearReferenceAfterSend,
  toggleChatReference,
  type ChatReferencePick,
} from "../../src/lib/chatReference.ts";

test("createTurnReferenceFields sends referenced_chat_id for a pick", () => {
  const ref: ChatReferencePick = { chatId: "chat-a-1", title: "Capital of Lebanon" };
  assert.deepEqual(createTurnReferenceFields(ref), { referenced_chat_id: "chat-a-1" });
});

test("createTurnReferenceFields preserves singular wire format for one array pick", () => {
  const refs: ChatReferencePick[] = [{ chatId: "chat-a", title: "A" }];
  assert.deepEqual(createTurnReferenceFields(refs), { referenced_chat_id: "chat-a" });
});

test("createTurnReferenceFields sends plural field for exactly two picks", () => {
  const refs: ChatReferencePick[] = [
    { chatId: "chat-a", title: "A" },
    { chatId: "chat-b", title: "B" },
  ];
  assert.deepEqual(createTurnReferenceFields(refs), {
    referenced_chat_ids: ["chat-a", "chat-b"],
  });
});

test("createTurnReferenceFields omits field when no reference", () => {
  assert.deepEqual(createTurnReferenceFields(null), {});
  assert.deepEqual(createTurnReferenceFields(undefined), {});
  assert.deepEqual(createTurnReferenceFields({ chatId: "  ", title: "x" }), {});
});

test("createTurnReferenceFields does not assemble handoff text or turns", () => {
  const payload = createTurnReferenceFields({ chatId: "abc", title: "Prior" });
  assert.equal(Object.keys(payload).length, 1);
  assert.equal("custom_instructions" in payload, false);
  assert.equal("mode" in payload, false);
});

test("shouldClearReferenceAfterSend only after successful createTurn", () => {
  assert.equal(shouldClearReferenceAfterSend(true), true);
  assert.equal(shouldClearReferenceAfterSend(false), false);
});

test("normal send without reference stays unchanged shape", () => {
  const body = {
    user_message: "Hello",
    model_set_id: "referee",
    attachment_ids: [] as string[],
    ...createTurnReferenceFields(null),
  };
  assert.deepEqual(body, {
    user_message: "Hello",
    model_set_id: "referee",
    attachment_ids: [],
  });
});

test("toggleChatReference prevents duplicates and enforces maximum two", () => {
  const a = { chatId: "a", title: "A" };
  const b = { chatId: "b", title: "B" };
  const c = { chatId: "c", title: "C" };
  const one = toggleChatReference([], a);
  assert.deepEqual(toggleChatReference(one, a), []);
  const two = toggleChatReference(one, b);
  assert.deepEqual(toggleChatReference(two, c), two);
});

test("toggleChatReference removes one selection without clearing the other", () => {
  const a = { chatId: "a", title: "A" };
  const b = { chatId: "b", title: "B" };
  assert.deepEqual(toggleChatReference([a, b], a), [b]);
});
