import assert from "node:assert/strict";
import test from "node:test";

import {
  getVerdictBookmarkState,
  removeSavedVerdictBySourceId,
  restoreSavedVerdictItem,
  savedVerdictCardView,
  updateVerdictSavedInTurns,
} from "../../src/lib/savedVerdicts.ts";

function turn(overrides = {}) {
  return {
    id: "turn-1",
    chat_id: "chat-1",
    user_message: "Prompt",
    model_set_id: "set",
    strategy: "Synthesize",
    verdict_model: "gemini",
    status: "completed",
    model_answers: [],
    verdict: {
      id: "verdict-1",
      model_id: "gemini",
      strategy: "Synthesize",
      text: "Verdict text",
      reason: "Verdict reason",
      saved: false,
      tokens_input: 0,
      tokens_output: 0,
      cost_usd: 0,
    },
    decision_insurance: null,
    created_at: "2026-07-22T00:00:00Z",
    ...overrides,
  };
}

function savedVerdict(overrides = {}) {
  return {
    id: "saved-1",
    source_verdict_id: "verdict-1",
    source_turn_id: "turn-1",
    source_chat_id: "chat-1",
    source_chat_title: "Snapshot chat",
    source_user_message: "Original prompt",
    verdict_text: "Complete verdict",
    verdict_reason: "Reason",
    verdict_model_id: "gemini",
    strategy: "Synthesize",
    saved_at: "2026-07-22T10:00:00Z",
    original_chat_exists: true,
    original_chat_route: "/chat?chatId=chat-1",
    ...overrides,
  };
}

test("completed verdict shows an unsaved bookmark", () => {
  const state = getVerdictBookmarkState(turn(), new Set());

  assert.equal(state.visible, true);
  assert.equal(state.saved, false);
  assert.equal(state.filled, false);
  assert.equal(state.label, "Save Verdict");
});

test("saving changes verdict to saved state", () => {
  const updated = updateVerdictSavedInTurns([turn()], "verdict-1", true);

  assert.equal(updated[0].verdict?.saved, true);
  assert.equal(getVerdictBookmarkState(updated[0], new Set()).filled, true);
});

test("saved state remains when data is reloaded", () => {
  const reloaded = turn({ verdict: { ...turn().verdict, saved: true } });
  const state = getVerdictBookmarkState(reloaded, new Set());

  assert.equal(state.saved, true);
  assert.equal(state.label, "Remove saved Verdict");
});

test("repeated clicking is disabled while pending", () => {
  const state = getVerdictBookmarkState(turn(), new Set(["verdict-1"]));

  assert.equal(state.disabled, true);
});

test("API failure restores the prior saved state", () => {
  const optimistic = updateVerdictSavedInTurns([turn()], "verdict-1", true);
  const restored = updateVerdictSavedInTurns(optimistic, "verdict-1", false);

  assert.equal(restored[0].verdict?.saved, false);
});

test("unsaving changes the icon back", () => {
  const saved = turn({ verdict: { ...turn().verdict, saved: true } });
  const updated = updateVerdictSavedInTurns([saved], "verdict-1", false);
  const state = getVerdictBookmarkState(updated[0], new Set());

  assert.equal(state.saved, false);
  assert.equal(state.filled, false);
});

test("unsaving from Saved Verdicts changes the cached original Verdict to saved false", () => {
  const cached = [turn({ verdict: { ...turn().verdict, saved: true } })];
  const item = savedVerdict({ id: "saved-row-1", source_verdict_id: "verdict-1" });

  const remaining = removeSavedVerdictBySourceId([item], item.source_verdict_id);
  const updatedTurns = updateVerdictSavedInTurns(cached, item.source_verdict_id, false);

  assert.equal(remaining.length, 0);
  assert.equal(updatedTurns[0].verdict?.saved, false);
});

test("navigating back to original chat shows an outline bookmark after Saved Verdicts unsave", () => {
  const cached = [turn({ verdict: { ...turn().verdict, saved: true } })];
  const updatedTurns = updateVerdictSavedInTurns(cached, "verdict-1", false);
  const bookmark = getVerdictBookmarkState(updatedTurns[0], new Set());

  assert.equal(bookmark.saved, false);
  assert.equal(bookmark.filled, false);
  assert.equal(bookmark.label, "Save Verdict");
});

test("unsaving one Verdict does not modify another Verdict", () => {
  const first = turn({ id: "turn-1", verdict: { ...turn().verdict, id: "verdict-1", saved: true } });
  const second = turn({
    id: "turn-2",
    verdict: { ...turn().verdict, id: "verdict-2", saved: true },
  });

  const updated = updateVerdictSavedInTurns([first, second], "verdict-1", false);

  assert.equal(updated[0].verdict?.saved, false);
  assert.equal(updated[1].verdict?.saved, true);
});

test("Saved Verdict removal matches source_verdict_id, not SavedVerdict row id", () => {
  const item = savedVerdict({ id: "saved-row-1", source_verdict_id: "verdict-1" });
  const wrong = removeSavedVerdictBySourceId([item], "saved-row-1");
  const right = removeSavedVerdictBySourceId([item], "verdict-1");

  assert.equal(wrong.length, 1);
  assert.equal(right.length, 0);
});

test("failed Saved Verdicts unsave restores both list item and cached bookmark state", () => {
  const item = savedVerdict({ id: "saved-row-1", source_verdict_id: "verdict-1" });
  const cached = [turn({ verdict: { ...turn().verdict, saved: true } })];

  const optimisticItems = removeSavedVerdictBySourceId([item], item.source_verdict_id);
  const optimisticTurns = updateVerdictSavedInTurns(cached, item.source_verdict_id, false);
  const restoredItems = restoreSavedVerdictItem(optimisticItems, item);
  const restoredTurns = updateVerdictSavedInTurns(optimisticTurns, item.source_verdict_id, true);

  assert.deepEqual(restoredItems.map((saved) => saved.id), ["saved-row-1"]);
  assert.equal(restoredTurns[0].verdict?.saved, true);
});

test("saving again changes the same cached Verdict back to saved true", () => {
  const cached = [turn({ verdict: { ...turn().verdict, saved: false } })];
  const updated = updateVerdictSavedInTurns(cached, "verdict-1", true);

  assert.equal(updated[0].verdict?.saved, true);
});

test("Saved Verdicts page view renders snapshot content", () => {
  const card = savedVerdictCardView(savedVerdict());

  assert.equal(card.title, "Snapshot chat");
  assert.equal(card.prompt, "Original prompt");
  assert.equal(card.verdictText, "Complete verdict");
  assert.equal(card.verdictReason, "Reason");
  assert.equal(card.modelId, "gemini");
  assert.equal(card.strategy, "Synthesize");
});

test("empty state renders when there are no saved verdict cards", () => {
  const items: ReturnType<typeof savedVerdictCardView>[] = [];

  assert.equal(items.length, 0);
});

test("removing one saved item preserves other items", () => {
  const items = [
    savedVerdict({ id: "saved-1", source_verdict_id: "verdict-1" }),
    savedVerdict({ id: "saved-2", source_verdict_id: "verdict-2" }),
  ];
  const remaining = removeSavedVerdictBySourceId(items, "verdict-1");

  assert.deepEqual(
    remaining.map((item) => item.id),
    ["saved-2"],
  );
});

test("original-chat link appears only when available", () => {
  assert.equal(savedVerdictCardView(savedVerdict()).canOpenOriginalChat, true);
  assert.equal(
    savedVerdictCardView(
      savedVerdict({ source_chat_id: null, original_chat_exists: false, original_chat_route: null }),
    ).canOpenOriginalChat,
    false,
  );
});
