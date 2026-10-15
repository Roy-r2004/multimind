import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { mapApiChat } from "../../src/lib/chatHistory.ts";
import {
  answerPinRequest,
  buildPinnedVerdictMenuItems,
  dropPinsForTurns,
  isAnswerPinned,
  isVerdictPinned,
  partitionPinsForApi,
  pinnedMenuLabel,
  pinnedVerdictLabel,
  type PinnedVerdict,
} from "../../src/lib/pinnedVerdicts.ts";
import { singleAiAnswerActionState } from "../../src/lib/singleAiAnswerActions.ts";

const root = join(dirname(fileURLToPath(import.meta.url)), "../..");
const chatSrc = readFileSync(join(root, "src/routes/chat.tsx"), "utf8");

const solo = singleAiAnswerActionState({
  answerCount: 1,
  hasVerdict: false,
  status: "completed",
  text: "Only this model answered.",
  answerId: "answer-1",
});

test("single-AI copy uses the model answer and does not invent a verdict", () => {
  assert.equal(solo.showCopy, true);
  assert.equal(solo.copyText, "Only this model answered.");
  assert.equal(solo.showPin, true);
  assert.equal(solo.answerId, "answer-1");
  assert.equal(
    singleAiAnswerActionState({
      answerCount: 1,
      hasVerdict: false,
      status: "completed",
      text: "   ",
      answerId: "answer-1",
    }).showCopy,
    false,
  );
  assert.match(chatSrc, /singleAiAnswerActionState/);
  assert.match(chatSrc, /successMessage="Answer copied"/);
  assert.match(chatSrc, /data-testid="single-ai-pin"/);
  assert.doesNotMatch(chatSrc, /fake verdict|Synthesizing verdict for one/i);
});

test("single-AI pin targets the answer and survives as its own records", () => {
  assert.deepEqual(answerPinRequest("chat-1", "answer-1", "pin"), {
    path: "/chats/chat-1/pinned-answers/answer-1",
    method: "PUT",
  });
  assert.deepEqual(answerPinRequest("chat-1", "answer-1", "unpin"), {
    path: "/chats/chat-1/pinned-answers/answer-1",
    method: "DELETE",
  });
  const pins: PinnedVerdict[] = [
    {
      id: "whole",
      verdictId: "",
      answerId: "answer-1",
      turnId: "turn-1",
      pinType: "answer",
    },
    {
      id: "sel-1",
      verdictId: "",
      answerId: "answer-1",
      turnId: "turn-1",
      pinType: "selection",
      selectedText: "Only this",
    },
    {
      id: "sel-2",
      verdictId: "",
      answerId: "answer-1",
      turnId: "turn-1",
      pinType: "selection",
      selectedText: "model answered",
    },
  ];
  assert.equal(isAnswerPinned(pins, "answer-1"), true);
  assert.equal(isAnswerPinned(pins, "answer-2"), false);
  assert.equal(buildPinnedVerdictMenuItems(pins, [{ id: "turn-1" } as never]).length, 3);
  assert.equal(pinnedVerdictLabel(pins[0]!, [{ id: "turn-1" } as never]), "Answer — Turn 1");
  const api = partitionPinsForApi(pins);
  assert.equal(api.pinned_verdicts.length, 0);
  assert.equal(api.pinned_answers.length, 3);
  const restored = mapApiChat({
    id: "chat-1",
    title: "Solo",
    updated_at: "2026-10-15T00:00:00Z",
    pinned_verdicts: [],
    pinned_answers: api.pinned_answers.map((pin) => ({
      id: pin.id,
      pin_type: pin.pin_type === "selection" ? "selection" : "answer",
      selected_text: pin.selected_text,
      answer_id: pin.answer_id!,
      turn_id: pin.turn_id,
    })),
  });
  assert.equal(isAnswerPinned(restored.pinnedVerdicts, "answer-1"), true);
  assert.equal(restored.pinnedVerdicts.filter((pin) => pin.pinType === "selection").length, 2);
});

test("regenerating a single-AI turn drops that turn's pins and the new answer is pinnable", () => {
  const pins: PinnedVerdict[] = [
    { id: "old", verdictId: "", answerId: "old-answer", turnId: "old-turn", pinType: "answer" },
    {
      id: "old-sel",
      verdictId: "",
      answerId: "old-answer",
      turnId: "old-turn",
      pinType: "selection",
      selectedText: "before",
    },
    { id: "kept", verdictId: "verdict-2", turnId: "other-turn", pinType: "verdict" },
  ];
  const remaining = dropPinsForTurns(pins, ["old-turn"]);
  assert.deepEqual(
    remaining.map((pin) => pin.id),
    ["kept"],
  );
  const fresh = singleAiAnswerActionState({
    answerCount: 1,
    hasVerdict: false,
    status: "completed",
    text: "Regenerated answer",
    answerId: "new-answer",
  });
  assert.equal(fresh.showCopy, true);
  assert.equal(fresh.showPin, true);
  assert.equal(isAnswerPinned(remaining, fresh.answerId ?? undefined), false);
  assert.equal(isAnswerPinned([...remaining, {
    verdictId: "",
    answerId: "new-answer",
    turnId: "new-turn",
    pinType: "answer",
  }], "new-answer"), true);
});

test("multi-AI turns keep Verdict copy and pin and do not add answer actions", () => {
  const council = singleAiAnswerActionState({
    answerCount: 3,
    hasVerdict: true,
    status: "completed",
    text: "One of the council answers",
    answerId: "answer-a",
  });
  assert.equal(council.showCopy, false);
  assert.equal(council.showPin, false);
  const oneWithVerdict = singleAiAnswerActionState({
    answerCount: 1,
    hasVerdict: true,
    status: "completed",
    text: "Still a council turn",
    answerId: "answer-a",
  });
  assert.equal(oneWithVerdict.showCopy, false);
  assert.equal(oneWithVerdict.showPin, false);
  const verdictPins = [{ verdictId: "V", turnId: "T", pinType: "verdict" as const }];
  assert.equal(isVerdictPinned(verdictPins, "V"), true);
  assert.equal(isAnswerPinned(verdictPins, "V"), false);
  assert.equal(pinnedMenuLabel(verdictPins), "Pinned verdicts");
  assert.equal(
    pinnedMenuLabel([{ verdictId: "", answerId: "A", turnId: "T", pinType: "answer" }]),
    "Pinned answers",
  );
  assert.match(chatSrc, /aria-label=\{isPinned \? "Unpin verdict" : "Pin verdict"\}/);
  assert.match(chatSrc, /<VerdictCopyButton/);
  const verdictBlock = chatSrc.slice(chatSrc.indexOf("const verdictBlock"));
  assert.match(verdictBlock, /VerdictCopyButton/);
  assert.doesNotMatch(verdictBlock.slice(0, verdictBlock.indexOf("const explanationSurface")), /single-ai-pin/);
});
