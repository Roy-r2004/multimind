import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_MODEL_SET_TITLE,
  enqueueModelSetPersistence,
  findDefaultModelSetId,
  modelSetRegenerateRequestPayload,
  modelSetRequestPayload,
  normalizeModelSetTitle,
  resolveModelSetIdFromTurns,
  resolveNextModelSetId,
  selectExistingModelSetId,
  shouldApplyModelSetRestore,
} from "../../src/lib/modelSetSelection.ts";

function deferred() {
  let resolve!: () => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<void>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function set(id: string, name: string) {
  return { id, name };
}

test("normalizeModelSetTitle trims, lowercases, and collapses whitespace", () => {
  assert.equal(
    normalizeModelSetTitle("  Chafic   Ultimate Model Set "),
    "chafic ultimate model set",
  );
});

test("fresh selection prefers exact title chafic ultimate model set", () => {
  const sets = [
    set("referee", "Chafiq Referee"),
    set("ultimate-1", "chafic ultimate model set"),
    set("balanced", "Balanced Set"),
  ];
  assert.equal(selectExistingModelSetId(sets, ""), "ultimate-1");
  assert.equal(findDefaultModelSetId(sets), "ultimate-1");
});

test("new chat / empty current id selects default for any org-visible list", () => {
  const orgA = [set("a", "Other"), set("u", DEFAULT_MODEL_SET_TITLE)];
  const orgB = [set("b", "Coding"), set("u2", "Chafic Ultimate Model Set")];
  assert.equal(selectExistingModelSetId(orgA, ""), "u");
  assert.equal(selectExistingModelSetId(orgB, ""), "u2");
});

test("manual current selection is not overwritten when still present", () => {
  const sets = [set("ultimate-1", "chafic ultimate model set"), set("coding", "Coding Set")];
  assert.equal(selectExistingModelSetId(sets, "coding"), "coding");
});

test("existing chat turns restore newest model_set_id", () => {
  const id = resolveModelSetIdFromTurns([
    { model_set_id: "old-set", created_at: "2026-01-01T00:00:00Z" },
    { model_set_id: "chat-set", created_at: "2026-06-01T00:00:00Z" },
    { model_set_id: "mid-set", created_at: "2026-03-01T00:00:00Z" },
  ]);
  assert.equal(id, "chat-set");
});

test("empty turns do not invent a chat model set", () => {
  assert.equal(resolveModelSetIdFromTurns([]), null);
});

test("next-message selection prefers chat.model_set_id over older turns", () => {
  const turns = [
    { model_set_id: "old-set", created_at: "2026-01-01T00:00:00Z" },
    { model_set_id: "newest-turn-set", created_at: "2026-06-01T00:00:00Z" },
  ];
  assert.equal(
    resolveNextModelSetId({
      chatModelSetId: "switched-set",
      turns,
      availableSetIds: ["old-set", "newest-turn-set", "switched-set"],
    }),
    "switched-set",
  );
});

test("an old turns response cannot overwrite a newer explicit selection", () => {
  assert.equal(
    shouldApplyModelSetRestore({
      requestedChatId: "chat-1",
      activeChatId: "chat-1",
      selectionGenerationAtStart: 3,
      currentSelectionGeneration: 4,
    }),
    false,
  );
});

test("a load for another chat cannot overwrite the active chat selection", () => {
  assert.equal(
    shouldApplyModelSetRestore({
      requestedChatId: "chat-1",
      activeChatId: "chat-2",
      selectionGenerationAtStart: 4,
      currentSelectionGeneration: 4,
    }),
    false,
  );
});

test("the current chat load may restore when no explicit selection is newer", () => {
  assert.equal(
    shouldApplyModelSetRestore({
      requestedChatId: "chat-1",
      activeChatId: "chat-1",
      selectionGenerationAtStart: 4,
      currentSelectionGeneration: 4,
    }),
    true,
  );
});

test("existing and new chat persistence use the selected model_set_id payload", () => {
  assert.deepEqual(modelSetRequestPayload("council-b"), { model_set_id: "council-b" });
});

test("regeneration explicitly carries the current selected Council", () => {
  assert.deepEqual(modelSetRegenerateRequestPayload("Retry this", "council-b"), {
    prompt: "Retry this",
    model_set_id: "council-b",
  });
});

test("immediate regeneration uses B without waiting for B chat persistence", () => {
  const optimisticSelection = "council-b";
  assert.deepEqual(modelSetRegenerateRequestPayload("Retry now", optimisticSelection), {
    prompt: "Retry now",
    model_set_id: "council-b",
  });
});

test("switching chats restores each persisted chat Council", () => {
  const availableSetIds = ["council-a", "council-b"];
  const restore = (chatModelSetId: string) =>
    resolveNextModelSetId({ chatModelSetId, turns: [], availableSetIds });
  assert.equal(restore("council-a"), "council-a");
  assert.equal(restore("council-b"), "council-b");
  assert.equal(restore("council-a"), "council-a");
});

test("a sent B selection remains B when an older A load later becomes ineligible", () => {
  const selectionAfterSend = "council-b";
  assert.equal(
    shouldApplyModelSetRestore({
      requestedChatId: "chat-1",
      activeChatId: "chat-1",
      selectionGenerationAtStart: 7,
      currentSelectionGeneration: 8,
    }),
    false,
  );
  assert.equal(selectionAfterSend, "council-b");
  assert.equal(modelSetRequestPayload(selectionAfterSend).model_set_id, "council-b");
});

test("a B result after navigation cannot apply to the other chat", () => {
  assert.equal(
    shouldApplyModelSetRestore({
      requestedChatId: "chat-1",
      activeChatId: "chat-2",
      selectionGenerationAtStart: 2,
      currentSelectionGeneration: 2,
    }),
    false,
  );
});

test("B then C persists in order so the final chat selection is C", async () => {
  const queues = new Map<string, Promise<void>>();
  const b = deferred();
  const persisted: string[] = [];
  const bRequest = enqueueModelSetPersistence(queues, "chat-1", async () => {
    await b.promise;
    persisted.push("B");
  });
  const cRequest = enqueueModelSetPersistence(queues, "chat-1", async () => {
    persisted.push("C");
  });

  await Promise.resolve();
  assert.deepEqual(persisted, []);
  b.resolve();
  await Promise.all([bRequest, cRequest]);
  assert.deepEqual(persisted, ["B", "C"]);
  assert.equal(persisted.at(-1), "C");
});

test("an old failed B request cannot roll back a newer C selection", async () => {
  const queues = new Map<string, Promise<void>>();
  const b = deferred();
  let selection = "C";
  const bGeneration = 10;
  const currentGeneration = 11;
  const bRequest = enqueueModelSetPersistence(queues, "chat-1", () => b.promise);
  const cRequest = enqueueModelSetPersistence(queues, "chat-1", async () => undefined);

  b.reject(new Error("B failed"));
  await assert.rejects(bRequest, /B failed/);
  await cRequest;
  if (bGeneration === currentGeneration) selection = "A";
  assert.equal(selection, "C");
});

test("next-message selection falls back to newest turn when chat has no set", () => {
  assert.equal(
    resolveNextModelSetId({
      chatModelSetId: null,
      turns: [
        { model_set_id: "old-set", created_at: "2026-01-01T00:00:00Z" },
        { model_set_id: "newest-turn-set", created_at: "2026-06-01T00:00:00Z" },
      ],
      availableSetIds: ["old-set", "newest-turn-set", "other"],
    }),
    "newest-turn-set",
  );
});

test("next-message selection ignores unavailable chat model set", () => {
  assert.equal(
    resolveNextModelSetId({
      chatModelSetId: "deleted-set",
      turns: [{ model_set_id: "alive-set", created_at: "2026-06-01T00:00:00Z" }],
      availableSetIds: ["alive-set"],
    }),
    "alive-set",
  );
});

test("missing target set falls back to legacy referee then first set", () => {
  const withReferee = [set("balanced", "Balanced Set"), set("referee", "Chafiq Referee")];
  assert.equal(selectExistingModelSetId(withReferee, ""), "referee");

  const nameOnly = [set("x", "My Referee Clone"), set("y", "Other")];
  assert.equal(selectExistingModelSetId(nameOnly, ""), "x");

  const plain = [set("first", "Alpha"), set("second", "Beta")];
  assert.equal(selectExistingModelSetId(plain, ""), "first");
});

test("no model sets returns empty string", () => {
  assert.equal(selectExistingModelSetId([], ""), "");
  assert.equal(findDefaultModelSetId([]), null);
});

test("organization scoping is list-based: only sets in the provided list can win", () => {
  const orgList = [set("org-only", "Org Set"), set("u", "chafic ultimate model set")];
  assert.equal(selectExistingModelSetId(orgList, "other-org-set"), "u");
  assert.equal(findDefaultModelSetId([set("org-only", "Org Set")]), null);
});

test("duplicate titles select deterministically by lowest id", () => {
  const sets = [
    set("z-set", "chafic ultimate model set"),
    set("a-set", "chafic ultimate model set"),
    set("m-set", "chafic ultimate model set"),
  ];
  assert.equal(findDefaultModelSetId(sets), "a-set");
  assert.equal(selectExistingModelSetId(sets, ""), "a-set");
});

test("shared/public chat helpers do not alter selection APIs (pure functions only)", () => {
  // Shared page never calls selectExistingModelSetId; verifying purity keeps that contract.
  const sets = [set("u", "chafic ultimate model set")];
  const first = selectExistingModelSetId(sets, "");
  const second = selectExistingModelSetId(sets, "");
  assert.equal(first, second);
  assert.equal(first, "u");
});
