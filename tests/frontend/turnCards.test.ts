import assert from "node:assert/strict";
import test from "node:test";

import { deriveTurnAnswerCards } from "../../src/lib/turnCards.ts";

function answer(overrides = {}) {
  return {
    model_id: "gemini",
    model_name: "Gemini 2.5 Pro",
    text: null,
    confidence: null,
    status: "pending",
    error_message: null,
    tokens_input: 0,
    tokens_output: 0,
    cost_usd: 0,
    ...overrides,
  };
}

function turn(overrides = {}) {
  return {
    status: "completed",
    model_answers: [],
    ...overrides,
  };
}

test("historical Gemini alias answer renders once without a pending placeholder", () => {
  const cards = deriveTurnAnswerCards(
    turn({
      model_answers: [
        answer({
          model_id: "or:google--gemini-2.5-pro",
          status: "completed",
          text: "Gemini saved answer",
        }),
      ],
    }),
    ["gemini"],
  );

  assert.equal(cards.length, 1);
  assert.equal(cards[0].modelId, "or:google--gemini-2.5-pro");
  assert.equal(cards[0].answer?.text, "Gemini saved answer");
  assert.equal(cards[0].status, "completed");
});

test("active Gemini aliases render as one card for one saved Gemini answer", () => {
  const cards = deriveTurnAnswerCards(
    turn({
      status: "running",
      model_answers: [
        answer({
          model_id: "or:google--gemini-2.5-pro",
          status: "completed",
          text: "Gemini saved answer",
        }),
      ],
    }),
    ["gemini", "or:google--gemini-2.5-pro"],
  );

  assert.equal(cards.length, 1);
  assert.equal(cards[0].modelId, "or:google--gemini-2.5-pro");
  assert.equal(cards[0].status, "completed");
});

test("active Gemini collision prefers completed alias over exact pending answer", () => {
  for (const modelAnswers of [
    [
      answer({ model_id: "gemini", status: "pending", text: "" }),
      answer({
        model_id: "or:google--gemini-2.5-pro",
        status: "completed",
        text: "Gemini completed answer",
      }),
    ],
    [
      answer({
        model_id: "or:google--gemini-2.5-pro",
        status: "completed",
        text: "Gemini completed answer",
      }),
      answer({ model_id: "gemini", status: "pending", text: "" }),
    ],
  ]) {
    const cards = deriveTurnAnswerCards(
      turn({
        status: "running",
        model_answers: modelAnswers,
      }),
      ["gemini"],
    );

    const loading = cards[0]?.status === "pending" || cards[0]?.status === "running";

    assert.equal(cards.length, 1);
    assert.equal(cards[0].modelId, "or:google--gemini-2.5-pro");
    assert.equal(cards[0].status, "completed");
    assert.equal(cards[0].answer?.text, "Gemini completed answer");
    assert.equal(loading, false);
  }
});

test("historical turns render persisted answers instead of the current model set", () => {
  const cards = deriveTurnAnswerCards(
    turn({
      model_answers: [
        answer({ model_id: "claude", model_name: "Claude", status: "completed", text: "A" }),
        answer({ model_id: "gpt-4.1", model_name: "GPT-4.1", status: "completed", text: "B" }),
      ],
    }),
    ["gemini", "grok"],
  );

  assert.deepEqual(
    cards.map((card) => card.modelId),
    ["claude", "gpt-4.1"],
  );
  assert.ok(cards.every((card) => card.status === "completed"));
});

test("historical turns with no saved answers do not invent pending cards", () => {
  const cards = deriveTurnAnswerCards(turn({ status: "completed" }), ["gemini", "claude"]);

  assert.deepEqual(cards, []);
});

test("live turns with no saved answer keep selected pending placeholders", () => {
  const cards = deriveTurnAnswerCards(turn({ status: "running" }), ["gemini", "claude"]);

  assert.deepEqual(
    cards.map((card) => [card.modelId, card.status, card.answer]),
    [
      ["gemini", "pending", undefined],
      ["claude", "pending", undefined],
    ],
  );
});

test("partially completed live turns keep completed answers and pending placeholders", () => {
  const cards = deriveTurnAnswerCards(
    turn({
      status: "running",
      model_answers: [answer({ model_id: "gemini", status: "completed", text: "Gemini done" })],
    }),
    ["gemini", "claude"],
  );

  assert.equal(cards.length, 2);
  assert.equal(cards[0].modelId, "gemini");
  assert.equal(cards[0].status, "completed");
  assert.equal(cards[0].answer?.text, "Gemini done");
  assert.equal(cards[1].modelId, "claude");
  assert.equal(cards[1].status, "pending");
  assert.equal(cards[1].answer, undefined);
});

test("failed historical answers render as failed and are not converted to pending", () => {
  const cards = deriveTurnAnswerCards(
    turn({
      status: "failed",
      model_answers: [
        answer({
          model_id: "google/gemini-2.5-pro",
          status: "failed",
          error_message: "Provider failed",
        }),
      ],
    }),
    ["gemini"],
  );

  assert.equal(cards.length, 1);
  assert.equal(cards[0].modelId, "google/gemini-2.5-pro");
  assert.equal(cards[0].status, "failed");
  assert.equal(cards[0].answer?.error_message, "Provider failed");
});
