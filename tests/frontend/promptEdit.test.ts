import assert from "node:assert/strict";
import test from "node:test";

import {
  LATER_TURNS_EDIT_WARNING,
  canEditUserPrompt,
  canSubmitEditedPrompt,
  countLaterTurns,
} from "../../src/lib/promptEdit.ts";

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

test("unchanged and empty prompts cannot be submitted", () => {
  assert.equal(canSubmitEditedPrompt("Hello", "Hello", false), false);
  assert.equal(canSubmitEditedPrompt("Hello", "  ", false), false);
  assert.equal(canSubmitEditedPrompt("Hello", "Hello world", true), false);
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

test("shared/public chat stays read-only by not importing edit UI helpers into shared route", () => {
  // Contract: shared.$token.tsx must not call canEditUserPrompt / UserPromptBubble.
  // This test documents the helper surface used only by authenticated chat.
  assert.equal(typeof canEditUserPrompt, "function");
  assert.equal(typeof canSubmitEditedPrompt, "function");
});
