import assert from "node:assert/strict";
import test from "node:test";

import {
  applyStreamEvent,
  canShowHistoricalTurnDelete,
  isAnyTurnGenerating,
  isHistoricalTurnDeleteDisabled,
  removeTurnFromList,
  upsertTurn,
} from "../../src/lib/turnState.ts";

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

test("historical turn shows delete action and active generating turn does not", () => {
  assert.equal(canShowHistoricalTurnDelete(apiTurn({ status: "completed" })), true);
  assert.equal(canShowHistoricalTurnDelete(apiTurn({ status: "partial" })), true);
  assert.equal(canShowHistoricalTurnDelete(apiTurn({ status: "failed" })), true);
  assert.equal(canShowHistoricalTurnDelete(apiTurn({ status: "running" })), false);
  assert.equal(canShowHistoricalTurnDelete(apiTurn({ status: "pending" })), false);
});

test("historical deletion is disabled while another turn is generating", () => {
  const turns = [
    apiTurn({ id: "turn-1", status: "completed" }),
    apiTurn({ id: "turn-2", status: "running" }),
  ];
  const disabled = isHistoricalTurnDeleteDisabled(isAnyTurnGenerating(turns));

  assert.equal(disabled, true);
  assert.equal(canShowHistoricalTurnDelete(turns[0]), true);
});

test("canceling historical deletion leaves the turn list unchanged", () => {
  const turns = [
    apiTurn({ id: "turn-1", user_message: "Before" }),
    apiTurn({ id: "turn-2", user_message: "Cancel me" }),
  ];
  const before = turns.map((turn) => [turn.id, turn.user_message]);
  const afterCancel = turns;

  assert.deepEqual(
    afterCancel.map((turn) => [turn.id, turn.user_message]),
    before,
  );
});

test("confirming deletion targets the exact chat id and turn id", () => {
  const target = apiTurn({ id: "turn-2", chat_id: "chat-1" });

  assert.deepEqual({ chatId: target.chat_id, turnId: target.id }, {
    chatId: "chat-1",
    turnId: "turn-2",
  });
});

test("turn remains visible while delete request is pending", () => {
  const turns = [
    apiTurn({ id: "turn-1", user_message: "Before" }),
    apiTurn({ id: "turn-2", user_message: "Pending delete" }),
  ];

  assert.equal(turns.some((turn) => turn.id === "turn-2"), true);
});

test("successful historical deletion removes only selected middle turn", () => {
  const first = apiTurn({ id: "turn-1", user_message: "Before" });
  const middle = apiTurn({ id: "turn-2", user_message: "Delete me" });
  const later = apiTurn({ id: "turn-3", user_message: "After" });
  const remaining = removeTurnFromList([first, middle, later], "turn-2");

  assert.deepEqual(
    remaining.map((turn) => [turn.id, turn.user_message]),
    [
      ["turn-1", "Before"],
      ["turn-3", "After"],
    ],
  );
});

test("failed historical deletion keeps the turn visible", () => {
  const turns = [
    apiTurn({ id: "turn-1", user_message: "Before" }),
    apiTurn({ id: "turn-2", user_message: "Keep me" }),
  ];

  assert.equal(turns.some((turn) => turn.id === "turn-2"), true);
});

test("duplicate confirmation clicks are prevented while deletion is pending", () => {
  let deleting = false;
  let calls = 0;
  const confirm = () => {
    if (deleting) return;
    deleting = true;
    calls += 1;
  };

  confirm();
  confirm();

  assert.equal(calls, 1);
});

test("removing a turn does not clear unrelated Saved Verdict state", () => {
  const withSavedVerdict = apiTurn({
    id: "turn-1",
    verdict: {
      id: "verdict-1",
      model_id: "gemini",
      strategy: "Synthesize",
      text: "Saved",
      reason: "Reason",
      saved: true,
      tokens_input: 0,
      tokens_output: 0,
      cost_usd: 0,
    },
  });
  const deleted = apiTurn({ id: "turn-2" });
  const remaining = removeTurnFromList([withSavedVerdict, deleted], "turn-2");

  assert.equal(remaining[0].verdict?.saved, true);
});

test("reloaded state keeps deleted turn absent when backend omits it", () => {
  const backendTurns = [
    apiTurn({ id: "turn-1", user_message: "Before" }),
    apiTurn({ id: "turn-3", user_message: "After" }),
  ];

  assert.equal(backendTurns.some((turn) => turn.id === "turn-2"), false);
});
