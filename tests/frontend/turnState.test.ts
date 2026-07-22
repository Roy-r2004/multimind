import assert from "node:assert/strict";
import test from "node:test";

import { applyStreamEvent, removeTurnFromList, upsertTurn } from "../../src/lib/turnState.ts";

function apiTurn(overrides = {}) {
  return {
    id: "turn-1",
    chat_id: "chat-1",
    user_message: "Question?",
    model_set_id: "set",
    strategy: "Synthesize",
    verdict_model: "gemini",
    status: "running",
    model_answers: [
      {
        model_id: "gpt-4.1",
        model_name: "GPT-4.1",
        text: "Done",
        confidence: 90,
        status: "completed",
        error_message: null,
        tokens_input: 1,
        tokens_output: 1,
        cost_usd: 0,
      },
    ],
    verdict: null,
    decision_insurance: null,
    created_at: "2026-07-21T00:00:00Z",
    ...overrides,
  };
}

test("removeTurnFromList removes only the selected active turn", () => {
  const first = apiTurn({ id: "turn-1", user_message: "Before" });
  const active = apiTurn({ id: "turn-2", user_message: "Remove me" });
  const next = apiTurn({ id: "turn-3", user_message: "After" });

  const remaining = removeTurnFromList([first, active, next], "turn-2");

  assert.deepEqual(
    remaining.map((turn) => [turn.id, turn.user_message]),
    [
      ["turn-1", "Before"],
      ["turn-3", "After"],
    ],
  );
});

test("removed turn is not modified by a late stream event when absent from state", () => {
  const list = removeTurnFromList([apiTurn({ id: "turn-1" })], "turn-1");
  const current = list.find((turn) => turn.id === "turn-1");

  assert.equal(current, undefined);
});

test("unrelated newer turn is not overwritten by old turn events", () => {
  const newer = apiTurn({ id: "turn-2", user_message: "New prompt", status: "running" });
  const oldUpdated = applyStreamEvent(apiTurn({ id: "turn-1" }), "model_answer_completed", {
    model_id: "gpt-4.1",
    text: "Late old answer",
    confidence: 80,
  });

  const list = upsertTurn([newer], oldUpdated).filter((turn) => turn.id !== oldUpdated.id);

  assert.equal(list.length, 1);
  assert.equal(list[0].id, "turn-2");
  assert.equal(list[0].user_message, "New prompt");
});
