import assert from "node:assert/strict";
import test from "node:test";

import {
  composerDraftStorageKey,
  readComposerDraft,
  transcriptInsertion,
  writeComposerDraft,
} from "../../src/lib/composerInput.ts";

test("draft storage keys distinguish new chats from existing chats", () => {
  assert.equal(composerDraftStorageKey(null), "multimind:draft:new");
  assert.equal(composerDraftStorageKey("chat-1"), "multimind:draft:chat-1");
});

test("draft persistence writes, restores, and clears by key", () => {
  const store = new Map<string, string>();
  const originalWindow = globalThis.window;
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: {
      localStorage: {
        getItem: (key: string) => store.get(key) ?? null,
        setItem: (key: string, value: string) => {
          store.set(key, value);
        },
        removeItem: (key: string) => {
          store.delete(key);
        },
      },
    },
  });
  try {
    const key = composerDraftStorageKey("chat-a");
    writeComposerDraft(key, "Hello council");
    assert.equal(readComposerDraft(key), "Hello council");
    writeComposerDraft(key, "");
    assert.equal(readComposerDraft(key), "");
    assert.equal(store.has(key), false);
  } finally {
    if (originalWindow === undefined) {
      Reflect.deleteProperty(globalThis, "window");
    } else {
      Object.defineProperty(globalThis, "window", {
        configurable: true,
        value: originalWindow,
      });
    }
  }
});

test("voice transcript appends as a paragraph without a selection", () => {
  assert.deepEqual(transcriptInsertion("Make it shorter.", "Add a table.", null, null), {
    value: "Make it shorter.\n\nAdd a table.",
    cursor: "Make it shorter.\n\nAdd a table.".length,
  });
});

test("voice transcript becomes the draft when the composer is empty", () => {
  assert.deepEqual(transcriptInsertion("", "Add a table.", null, null), {
    value: "Add a table.",
    cursor: "Add a table.".length,
  });
});

test("voice transcript replaces the current selection", () => {
  const current = "Hello world";
  assert.deepEqual(transcriptInsertion(current, "there", 6, 11), {
    value: "Hello there",
    cursor: "Hello there".length,
  });
});
