import assert from "node:assert/strict";
import test from "node:test";

import {
  createTurnReferenceFields,
  shouldClearReferenceAfterSend,
  type ChatReferencePick,
} from "../../src/lib/chatReference.ts";

test("createTurnReferenceFields sends referenced_chat_id for a pick", () => {
  const ref: ChatReferencePick = { chatId: "chat-a-1", title: "Capital of Lebanon" };
  assert.deepEqual(createTurnReferenceFields(ref), { referenced_chat_id: "chat-a-1" });
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
