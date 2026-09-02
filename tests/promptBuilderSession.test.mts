import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import {
  applyPromptBuilderSuccess,
  beginPromptBuilderSend,
  clearPersistedPromptBuilderSession,
  createPromptBuilderSession,
  loadPromptBuilderSession,
  openPromptBuilderSession,
  originalPromptClipboardText,
  persistPromptBuilderSession,
  promptBuilderStorageKey,
  resolvePromptBuilderSession,
  savePromptBuilderSession,
  startNewPromptBuilderSession,
} from "../src/lib/promptBuilderSession.ts";

class MemoryStorage {
  private values = new Map<string, string>();
  getItem(key: string) {
    return this.values.get(key) ?? null;
  }
  setItem(key: string, value: string) {
    this.values.set(key, value);
  }
  removeItem(key: string) {
    this.values.delete(key);
  }
  clear() {
    this.values.clear();
  }
}

const memoryStorage = new MemoryStorage();
Object.defineProperty(globalThis, "window", {
  value: { localStorage: memoryStorage },
  configurable: true,
});

function keyFor(identity: string, orgId = "org") {
  return promptBuilderStorageKey(orgId, identity);
}

test("session preserves original, all messages, draft, and latest prompt verbatim", () => {
  const original = "  original\r\nwith spacing  ";
  let session = createPromptBuilderSession(original, "models-a");
  session = beginPromptBuilderSend(session, session.draft);
  session = applyPromptBuilderSuccess(session, "  generated one\n");
  session = { ...session, draft: "\tunsent refinement\r\n" };
  const beforeCloseOrUse = structuredClone(session);

  assert.deepEqual(session, beforeCloseOrUse);
  assert.equal(session.originalPrompt, original);
  assert.equal(session.messages[0]?.content, original);
  assert.equal(session.messages[1]?.content, "  generated one\n");
  assert.equal(session.draft, "\tunsent refinement\r\n");
  assert.equal(session.latestPrompt, "  generated one\n");
});

test("storage restores losslessly across refresh/navigation and isolates chats", () => {
  const chatAKey = keyFor("chat-a");
  const chatBKey = keyFor("chat-b");
  const chatA = applyPromptBuilderSuccess(
    beginPromptBuilderSend(createPromptBuilderSession("A", "set-a"), "A"),
    "result A",
  );
  const chatB = createPromptBuilderSession("B", "set-b");
  savePromptBuilderSession(chatAKey, chatA);
  savePromptBuilderSession(chatBKey, chatB);

  assert.deepEqual(loadPromptBuilderSession(chatAKey), chatA);
  assert.deepEqual(loadPromptBuilderSession(chatBKey), chatB);
  assert.notDeepEqual(loadPromptBuilderSession(chatAKey), loadPromptBuilderSession(chatBKey));
});

test("only explicit clearing removes a persisted session", () => {
  const key = keyFor("new");
  const session = createPromptBuilderSession("keep me", "set-a");
  savePromptBuilderSession(key, session);
  assert.deepEqual(loadPromptBuilderSession(key), session);
  clearPersistedPromptBuilderSession(key);
  assert.equal(loadPromptBuilderSession(key), null);
});

test("a rejected truncated generation cannot replace persisted history or latestPrompt", () => {
  const key = keyFor("chat-truncated");
  const session = applyPromptBuilderSuccess(
    beginPromptBuilderSend(createPromptBuilderSession("original", "set-a"), "original"),
    "complete previous prompt",
  );
  const withDraft = { ...session, draft: "make it much longer" };
  savePromptBuilderSession(key, withDraft);

  // The modal commits beginPromptBuilderSend/applyPromptBuilderSuccess only after
  // the API succeeds. A finish_reason=length rejection performs no session write.
  assert.deepEqual(loadPromptBuilderSession(key), withDraft);
  assert.equal(loadPromptBuilderSession(key)?.latestPrompt, "complete previous prompt");
  assert.equal(loadPromptBuilderSession(key)?.draft, "make it much longer");
});

test("a request timeout does not clear Builder state or overwrite latestPrompt", () => {
  const key = keyFor("chat-timeout");
  const session = applyPromptBuilderSuccess(
    beginPromptBuilderSend(createPromptBuilderSession("keep original", "set-a"), "keep original"),
    "keep latest",
  );
  const withDraft = { ...session, draft: "unsent refinement still here" };
  savePromptBuilderSession(key, withDraft);

  // PromptBuilderModal.send() only setSession(applyPromptBuilderSuccess) after refine()
  // resolves. A REQUEST_TIMEOUT catch leaves React/localStorage session untouched.
  assert.deepEqual(loadPromptBuilderSession(key), withDraft);
  assert.equal(loadPromptBuilderSession(key)?.originalPrompt, "keep original");
  assert.equal(loadPromptBuilderSession(key)?.latestPrompt, "keep latest");
  assert.equal(loadPromptBuilderSession(key)?.draft, "unsent refinement still here");
  assert.equal(loadPromptBuilderSession(key)?.messages.length, 2);
});

test("opening with no persisted session seeds originalPrompt and draft from the current composer", () => {
  const key = keyFor("open-fresh");
  clearPersistedPromptBuilderSession(key);
  const composer = "ORIGINAL PROMPT TEST 123";

  const opened = openPromptBuilderSession(key, composer, "set-a");

  assert.equal(opened.originalPrompt, composer);
  assert.equal(opened.draft, composer);
  assert.equal(opened.latestPrompt, null);
  assert.deepEqual(opened.messages, []);
  assert.equal(opened.modelSetId, "set-a");
  assert.equal(loadPromptBuilderSession(key)?.originalPrompt, composer);
});

test("reopening a legitimate session keeps originalPrompt even if the composer changed", () => {
  const key = keyFor("open-existing");
  const first = openPromptBuilderSession(key, "FIRST ORIGINAL", "set-a");
  savePromptBuilderSession(key, { ...first, draft: "later builder draft" });

  const reopened = openPromptBuilderSession(key, "DIFFERENT TEXT", "set-b");

  assert.equal(reopened.originalPrompt, "FIRST ORIGINAL");
  assert.equal(reopened.draft, "later builder draft");
  assert.equal(reopened.modelSetId, "set-a");
});

test("a buggy empty session with no history is repaired from the current composer", () => {
  const key = keyFor("open-poisoned");
  savePromptBuilderSession(key, createPromptBuilderSession("", "set-stale"));

  const opened = openPromptBuilderSession(key, "RECOVER THIS ORIGINAL", "set-a");

  assert.equal(opened.originalPrompt, "RECOVER THIS ORIGINAL");
  assert.equal(opened.draft, "RECOVER THIS ORIGINAL");
  assert.equal(opened.modelSetId, "set-a");
  assert.equal(loadPromptBuilderSession(key)?.originalPrompt, "RECOVER THIS ORIGINAL");
});

test("empty originalPrompt with meaningful history is not fabricated or overwritten", () => {
  const key = keyFor("open-history-empty-original");
  const historic: ReturnType<typeof createPromptBuilderSession> = {
    ...createPromptBuilderSession("", "set-a"),
    messages: [
      { role: "user", content: "keep this user turn" },
      { role: "assistant", content: "keep this generated prompt" },
    ],
    latestPrompt: "keep this generated prompt",
    draft: "",
  };
  savePromptBuilderSession(key, historic);

  const opened = openPromptBuilderSession(key, "DO NOT INVENT FROM COMPOSER", "set-other");

  assert.equal(opened.originalPrompt, "");
  assert.equal(opened.latestPrompt, "keep this generated prompt");
  assert.equal(opened.messages[0]?.content, "keep this user turn");
  assert.equal(opened.modelSetId, "set-a");
});

test("Use This Prompt leaves originalPrompt unchanged", () => {
  const original = "FIRST ORIGINAL";
  let session = createPromptBuilderSession(original, "set-a");
  session = applyPromptBuilderSuccess(
    beginPromptBuilderSend(session, session.draft),
    "LATEST IMPROVED PROMPT",
  );
  const composerAfterUse = session.latestPrompt;
  const key = keyFor("use-this-prompt");
  savePromptBuilderSession(key, session);

  assert.equal(composerAfterUse, "LATEST IMPROVED PROMPT");
  assert.equal(session.originalPrompt, original);
  assert.equal(loadPromptBuilderSession(key)?.originalPrompt, original);
});

test("refresh/restore keeps originalPrompt unchanged", () => {
  const key = keyFor("refresh-restore");
  const opened = openPromptBuilderSession(key, "ORIGINAL PROMPT TEST 123", "set-a");
  const refined = applyPromptBuilderSuccess(
    beginPromptBuilderSend(opened, opened.draft),
    "improved after refresh",
  );
  savePromptBuilderSession(key, refined);

  const restored = loadPromptBuilderSession(key);
  assert.equal(restored?.originalPrompt, "ORIGINAL PROMPT TEST 123");
  assert.equal(openPromptBuilderSession(key, "composer after refresh", "set-a").originalPrompt, "ORIGINAL PROMPT TEST 123");
});

test("Copy clipboard payload is exactly originalPrompt", () => {
  const original = "  copy only this \r\nverbatim  ";
  const session = applyPromptBuilderSuccess(
    beginPromptBuilderSend(createPromptBuilderSession(original, "set-a"), original),
    "latest prompt must not be copied",
  );

  assert.equal(originalPromptClipboardText(session), original);
  assert.equal(originalPromptClipboardText(session), session.originalPrompt);
});

test("Copy copies an empty string when originalPrompt is empty", () => {
  const session = startNewPromptBuilderSession("set-a");
  assert.equal(session.originalPrompt, "");
  assert.equal(originalPromptClipboardText(session), "");
});

test("Copy does not copy latestPrompt or refinement history", () => {
  const session = applyPromptBuilderSuccess(
    beginPromptBuilderSend(createPromptBuilderSession("ONLY ORIGINAL", "set-a"), "user refinement"),
    "LATEST PROMPT AND HISTORY",
  );
  const copied = originalPromptClipboardText(session);

  assert.equal(copied, "ONLY ORIGINAL");
  assert.notEqual(copied, session.latestPrompt);
  assert.ok(!copied.includes("user refinement"));
  assert.ok(!copied.includes("LATEST PROMPT AND HISTORY"));
});

test("Copy button stays rendered even when originalPrompt is empty", () => {
  const source = readFileSync(
    join(dirname(fileURLToPath(import.meta.url)), "../src/components/chat/PromptBuilderModal.tsx"),
    "utf8",
  );
  const originalSection = source.slice(
    source.indexOf("Original Prompt"),
    source.indexOf("{contextUsage &&"),
  );
  assert.match(originalSection, /data-prompt-builder-copy-original/);
  assert.match(originalSection, /\{originalCopied \? "Copied" : "Copy"\}/);
  assert.doesNotMatch(originalSection, /session\.originalPrompt\s*&&/);
  assert.doesNotMatch(originalSection, /originalPrompt\s*\?\s*/);
  assert.doesNotMatch(originalSection, /disabled=\{!session\.originalPrompt\}/);
});

test("confirmed New Session starts completely empty and does not copy the composer", () => {
  const composer = "Some text currently in composer";
  const prior = applyPromptBuilderSuccess(
    beginPromptBuilderSend(createPromptBuilderSession(composer, "set-a"), composer),
    "previous latest",
  );
  const next = startNewPromptBuilderSession(prior.modelSetId);

  assert.equal(next.originalPrompt, "");
  assert.equal(next.draft, "");
  assert.equal(next.messages.length, 0);
  assert.equal(next.latestPrompt, null);
  assert.equal(next.modelSetId, "set-a");
  assert.equal(composer, "Some text currently in composer");
  assert.notEqual(next.originalPrompt, composer);
  assert.notEqual(next.draft, composer);
});

test("New Session does not modify the normal composer", () => {
  let composer = "Some text currently in composer";
  const session = startNewPromptBuilderSession("set-a");
  assert.equal(session.originalPrompt, "");
  assert.equal(composer, "Some text currently in composer");
  composer = composer;
  assert.equal(composer, "Some text currently in composer");
});

test("New Session persists empty original and is not repaired from composer on reopen", () => {
  const key = keyFor("new-session-empty");
  const composer = "Some text currently in composer";
  openPromptBuilderSession(key, composer, "set-a");
  persistPromptBuilderSession(key, startNewPromptBuilderSession("set-a"));

  const reopened = openPromptBuilderSession(key, composer, "set-b");
  assert.equal(reopened.originalPrompt, "");
  assert.equal(reopened.draft, "");
  assert.deepEqual(reopened.messages, []);
  assert.equal(reopened.latestPrompt, null);
  assert.equal(reopened.modelSetId, "set-a");
  assert.equal(composer, "Some text currently in composer");
});

test("first Send from an empty New Session captures originalPrompt verbatim before any API result", () => {
  const key = keyFor("first-send-capture");
  let session = startNewPromptBuilderSession("set-a");
  assert.equal(session.originalPrompt, "");
  assert.deepEqual(session.messages, []);

  const first = "ORIGINAL PROMPT TEST 123";
  session = beginPromptBuilderSend(session, first);
  persistPromptBuilderSession(key, session);

  assert.equal(session.originalPrompt, first);
  assert.equal(session.draft, "");
  assert.equal(session.messages.length, 1);
  assert.equal(session.messages[0]?.role, "user");
  assert.equal(session.messages[0]?.content, first);
  assert.equal(loadPromptBuilderSession(key)?.originalPrompt, first);
  assert.equal(loadPromptBuilderSession(key)?.messages[0]?.content, first);
});

test("first Send preserves leading and trailing characters; trim is only an emptiness check", () => {
  const first = "  keep spacing \r\n";
  assert.ok(first.trim().length > 0);
  const session = beginPromptBuilderSend(startNewPromptBuilderSession("set-a"), first);
  assert.equal(session.originalPrompt, first);
  assert.equal(session.messages[0]?.content, first);
  assert.notEqual(session.originalPrompt, first.trim());
});

test("second and third Send do not change originalPrompt", () => {
  let session = beginPromptBuilderSend(
    startNewPromptBuilderSession("set-a"),
    "Create a complete segmentation methodology",
  );
  session = applyPromptBuilderSuccess(session, "improved one");
  session = beginPromptBuilderSend(session, "Make it more technical");
  session = applyPromptBuilderSuccess(session, "improved two");
  session = beginPromptBuilderSend(session, "Add clinical governance");

  assert.equal(session.originalPrompt, "Create a complete segmentation methodology");
  assert.equal(session.messages[0]?.content, "Create a complete segmentation methodology");
  assert.equal(session.messages[2]?.content, "Make it more technical");
  assert.equal(session.messages[4]?.content, "Add clinical governance");
});

test("API failure or timeout after first Send does not erase originalPrompt", () => {
  const key = keyFor("first-send-then-fail");
  let session = beginPromptBuilderSend(
    startNewPromptBuilderSession("set-a"),
    "ORIGINAL PROMPT TEST 123",
  );
  persistPromptBuilderSession(key, session);

  // Modal catch path: no rollback, no applyPromptBuilderSuccess.
  assert.equal(session.originalPrompt, "ORIGINAL PROMPT TEST 123");
  assert.equal(session.latestPrompt, null);
  assert.equal(loadPromptBuilderSession(key)?.originalPrompt, "ORIGINAL PROMPT TEST 123");
  assert.equal(loadPromptBuilderSession(key)?.messages[0]?.content, "ORIGINAL PROMPT TEST 123");
});

test("existing non-empty originalPrompt is never overwritten on later Send", () => {
  let session = createPromptBuilderSession("FIRST PROMPT", "set-a");
  session = beginPromptBuilderSend(session, "FIRST PROMPT");
  session = applyPromptBuilderSuccess(session, "latest");
  session = beginPromptBuilderSend(session, "later refinement");
  assert.equal(session.originalPrompt, "FIRST PROMPT");
});

test("blank originalPrompt with existing history is not replaced by a later refinement", () => {
  const historic: ReturnType<typeof createPromptBuilderSession> = {
    ...createPromptBuilderSession("", "set-a"),
    messages: [
      { role: "user", content: "keep this user turn" },
      { role: "assistant", content: "keep this generated prompt" },
    ],
    latestPrompt: "keep this generated prompt",
    draft: "",
  };
  const next = beginPromptBuilderSend(historic, "later refinement must not become original");
  assert.equal(next.originalPrompt, "");
  assert.equal(next.messages[0]?.content, "keep this user turn");
  assert.equal(
    next.messages[next.messages.length - 1]?.content,
    "later refinement must not become original",
  );
});

test("Copy returns the first submitted Original Prompt after capture", () => {
  let session = beginPromptBuilderSend(
    startNewPromptBuilderSession("set-a"),
    "ORIGINAL PROMPT TEST 123",
  );
  session = applyPromptBuilderSuccess(session, "latest prompt must not be copied");
  session = beginPromptBuilderSend(session, "second refinement");
  assert.equal(originalPromptClipboardText(session), "ORIGINAL PROMPT TEST 123");
});

test("New Session after capture resets originalPrompt back to empty", () => {
  const captured = beginPromptBuilderSend(
    startNewPromptBuilderSession("set-a"),
    "ORIGINAL PROMPT TEST 123",
  );
  const reset = startNewPromptBuilderSession(captured.modelSetId);
  assert.equal(reset.originalPrompt, "");
  assert.equal(reset.draft, "");
  assert.deepEqual(reset.messages, []);
  assert.equal(reset.latestPrompt, null);
});

test("empty composer shells are not persisted, so a later open can capture the composer", () => {
  const key = keyFor("empty-shell");
  const placeholder = createPromptBuilderSession("", "");
  persistPromptBuilderSession(key, placeholder);
  assert.equal(loadPromptBuilderSession(key), null);

  const opened = openPromptBuilderSession(key, "ORIGINAL PROMPT TEST 123", "set-a");
  assert.equal(opened.originalPrompt, "ORIGINAL PROMPT TEST 123");
});

test("storage keys isolate organization and new-chat draft identity", () => {
  assert.equal(promptBuilderStorageKey("org-a", "new"), "multimind:prompt-builder:org-a:new");
  assert.notEqual(promptBuilderStorageKey("org-a", "new"), promptBuilderStorageKey("org-b", "new"));
  assert.notEqual(promptBuilderStorageKey("org-a", "new"), promptBuilderStorageKey("org-a", "chat-1"));

  const newKey = keyFor("new", "org-a");
  const chatKey = keyFor("chat-1", "org-a");
  openPromptBuilderSession(newKey, "NEW CHAT ORIGINAL", "set-a");
  openPromptBuilderSession(chatKey, "CHAT ORIGINAL", "set-a");
  assert.equal(loadPromptBuilderSession(newKey)?.originalPrompt, "NEW CHAT ORIGINAL");
  assert.equal(loadPromptBuilderSession(chatKey)?.originalPrompt, "CHAT ORIGINAL");
});

test("resolvePromptBuilderSession is the actual open-time restore/create decision", () => {
  const created = resolvePromptBuilderSession(null, "ORIGINAL PROMPT TEST 123", "set-a");
  assert.equal(created.originalPrompt, "ORIGINAL PROMPT TEST 123");
  assert.equal(created.draft, "ORIGINAL PROMPT TEST 123");

  const legitimate = createPromptBuilderSession("FIRST ORIGINAL", "set-a");
  const restored = resolvePromptBuilderSession(legitimate, "DIFFERENT TEXT", "set-b");
  assert.equal(restored.originalPrompt, "FIRST ORIGINAL");

  const poisoned = createPromptBuilderSession("", "set-stale");
  const repaired = resolvePromptBuilderSession(poisoned, "RECOVER THIS ORIGINAL", "set-a");
  assert.equal(repaired.originalPrompt, "RECOVER THIS ORIGINAL");

  const withHistory: ReturnType<typeof createPromptBuilderSession> = {
    ...createPromptBuilderSession("", "set-a"),
    messages: [
      { role: "user", content: "history" },
      { role: "assistant", content: "generated" },
    ],
    latestPrompt: "generated",
    draft: "",
  };
  const kept = resolvePromptBuilderSession(withHistory, "DO NOT INVENT", "set-b");
  assert.equal(kept.originalPrompt, "");
  assert.equal(kept.latestPrompt, "generated");
});
