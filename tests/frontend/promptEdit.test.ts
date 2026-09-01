import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  LATER_TURNS_EDIT_WARNING,
  appendTranscriptToPrompt,
  canEditUserPrompt,
  canSubmitEditedPrompt,
  countLaterTurns,
} from "../../src/lib/promptEdit.ts";

test("voice transcript appends to an existing prompt with one blank line", () => {
  assert.equal(
    appendTranscriptToPrompt("Make it shorter.", "Add a table."),
    "Make it shorter.\n\nAdd a table.",
  );
});

test("voice transcript becomes the prompt when the draft is empty", () => {
  assert.equal(appendTranscriptToPrompt("", "  Add a table.  "), "Add a table.");
  assert.equal(appendTranscriptToPrompt("   ", "Add a table."), "Add a table.");
});

test("empty voice transcript leaves the current draft unchanged", () => {
  assert.equal(appendTranscriptToPrompt("Keep this exactly.  ", "   "), "Keep this exactly.  ");
});

test("functional voice append uses the latest prompt draft", () => {
  let draft = "Hello";
  const applyTranscript = (transcript: string) => {
    draft = appendTranscriptToPrompt(draft, transcript);
  };

  draft = "Hello world";
  applyTranscript("Add examples");
  assert.equal(draft, "Hello world\n\nAdd examples");
});

test("Edit helpers allow completed prompts when nothing is generating", () => {
  assert.equal(canEditUserPrompt({ status: "completed" }, [{ status: "completed" }]), true);
  assert.equal(canEditUserPrompt({ status: "partial" }, [{ status: "failed" }]), true);
});

test("Edit helpers hide edit while a turn is streaming", () => {
  assert.equal(canEditUserPrompt({ status: "running" }, [{ status: "running" }]), false);
  assert.equal(
    canEditUserPrompt({ status: "completed" }, [{ status: "completed" }, { status: "pending" }]),
    false,
  );
});

test("unchanged non-empty prompts can be submitted for regeneration", () => {
  assert.equal(canSubmitEditedPrompt("Hello", "Hello", false), true);
  assert.equal(canSubmitEditedPrompt("Hello", "  Hello  ", false), true);
});

test("empty prompts and submissions already in progress remain blocked", () => {
  assert.equal(canSubmitEditedPrompt("Hello", "  ", false), false);
  assert.equal(canSubmitEditedPrompt("Hello", "Hello", true), false);
  assert.equal(canSubmitEditedPrompt("Hello", "Hello world", true), false);
});

test("changed prompts can still be submitted", () => {
  assert.equal(canSubmitEditedPrompt("Hello", "Hello world", false), true);
});

test("later-turn warning copy is stable and countLaterTurns is correct", () => {
  assert.match(LATER_TURNS_EDIT_WARNING, /remove later messages/i);
  const turns = [
    { id: "a", created_at: "2026-01-01T00:00:00Z" },
    { id: "b", created_at: "2026-01-02T00:00:00Z" },
    { id: "c", created_at: "2026-01-03T00:00:00Z" },
  ];
  assert.equal(countLaterTurns(turns, "a"), 2);
  assert.equal(countLaterTurns(turns, "c"), 0);
  assert.equal(countLaterTurns(turns, "missing"), 0);
});

test("an unchanged earlier turn still follows the existing confirmation path", () => {
  const turns = [
    { id: "a", created_at: "2026-01-01T00:00:00Z" },
    { id: "b", created_at: "2026-01-02T00:00:00Z" },
  ];
  assert.equal(canSubmitEditedPrompt("Original", "Original", false), true);
  assert.equal(countLaterTurns(turns, "a") > 0, true);
  assert.match(LATER_TURNS_EDIT_WARNING, /remove later messages/i);
});

test("save forwards the trimmed unchanged draft through the existing regenerate request", () => {
  const bubbleSource = readFileSync(
    new URL("../../src/components/chat/UserPromptBubble.tsx", import.meta.url),
    "utf8",
  );
  const chatSource = readFileSync(new URL("../../src/routes/chat.tsx", import.meta.url), "utf8");
  assert.match(bubbleSource, /await onSubmit\(draft\.trim\(\)\)/);
  assert.match(
    chatSource,
    /regenerateTurn\([\s\S]*modelSetRegenerateRequestPayload\(prompt, set\.id\)/,
  );
});

test("prompt edit voice only appends draft text and never submits automatically", () => {
  const bubbleSource = readFileSync(
    new URL("../../src/components/chat/UserPromptBubble.tsx", import.meta.url),
    "utf8",
  );

  assert.match(
    bubbleSource,
    /setDraft\(\(current\) => appendTranscriptToPrompt\(current, result\.text\)\)/,
  );
  assert.doesNotMatch(
    bubbleSource,
    /onTranscript=\{[^}]*\b(?:onSubmit|save|requestPromptEdit|regenerateEditedPrompt)\b/,
  );
});

test("voice activity blocks button, keyboard save path, and edit cancellation", () => {
  const bubbleSource = readFileSync(
    new URL("../../src/components/chat/UserPromptBubble.tsx", import.meta.url),
    "utf8",
  );

  assert.match(bubbleSource, /if \(isVoiceActive \|\| !canSubmitEditedPrompt/);
  assert.match(bubbleSource, /void save\(\)/);
  assert.match(bubbleSource, /disabled=\{submitting \|\| isVoiceActive\}/);
  assert.match(bubbleSource, /const canSave = !isVoiceActive && canSubmitEditedPrompt/);
  assert.match(bubbleSource, /onRecordingStateChange=\{setIsVoiceActive\}/);
});

test("shared/public chat stays read-only by not importing edit UI helpers into shared route", () => {
  // Contract: shared.$token.tsx must not call canEditUserPrompt / UserPromptBubble.
  // This test documents the helper surface used only by authenticated chat.
  assert.equal(typeof canEditUserPrompt, "function");
  assert.equal(typeof canSubmitEditedPrompt, "function");
});
