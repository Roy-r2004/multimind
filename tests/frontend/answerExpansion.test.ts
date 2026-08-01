import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import {
  INITIAL_TURN_ANSWER_EXPANSION,
  isTurnAnswerExpanded,
  resetTurnAnswerExpansionForLayoutChange,
  toggleTurnAnswerExpansion,
  verticalExpansionIsIndependent,
} from "../../src/lib/answerExpansion.ts";

const root = join(dirname(fileURLToPath(import.meta.url)), "../..");

test("Vertical mode: expanding one answer does not expand another", () => {
  let state = { ...INITIAL_TURN_ANSWER_EXPANSION };
  state = toggleTurnAnswerExpansion({
    layout: "vertical",
    answerId: "a1",
    ...state,
  });
  assert.equal(
    isTurnAnswerExpanded({
      layout: "vertical",
      answerId: "a1",
      hasVerdict: true,
      ...state,
    }),
    true,
  );
  assert.equal(
    isTurnAnswerExpanded({
      layout: "vertical",
      answerId: "a2",
      hasVerdict: true,
      ...state,
    }),
    false,
  );
  assert.equal(verticalExpansionIsIndependent(state.expandedAnswerId, "a1", "a2"), true);
});

test("Vertical mode: collapsing one answer leaves others unchanged", () => {
  let state = toggleTurnAnswerExpansion({
    layout: "vertical",
    answerId: "a1",
    ...INITIAL_TURN_ANSWER_EXPANSION,
  });
  state = toggleTurnAnswerExpansion({
    layout: "vertical",
    answerId: "a1",
    ...state,
  });
  assert.equal(state.expandedAnswerId, null);
  assert.equal(
    isTurnAnswerExpanded({
      layout: "vertical",
      answerId: "a2",
      hasVerdict: true,
      ...state,
    }),
    false,
  );
});

test("Horizontal mode: expanding any answer expands all in the turn", () => {
  let state = toggleTurnAnswerExpansion({
    layout: "horizontal",
    answerId: "a2",
    ...INITIAL_TURN_ANSWER_EXPANSION,
  });
  assert.equal(state.allAnswersExpanded, true);
  for (const id of ["a1", "a2", "a3", "a4", "a5"]) {
    assert.equal(
      isTurnAnswerExpanded({
        layout: "horizontal",
        answerId: id,
        hasVerdict: true,
        ...state,
      }),
      true,
      id,
    );
  }
});

test("Horizontal mode: collapsing any answer collapses all in the turn", () => {
  let state = toggleTurnAnswerExpansion({
    layout: "horizontal",
    answerId: "a1",
    ...INITIAL_TURN_ANSWER_EXPANSION,
  });
  state = toggleTurnAnswerExpansion({
    layout: "horizontal",
    answerId: "a3",
    ...state,
  });
  assert.equal(state.allAnswersExpanded, false);
  assert.equal(
    isTurnAnswerExpanded({
      layout: "horizontal",
      answerId: "a1",
      hasVerdict: true,
      ...state,
    }),
    false,
  );
});

test("expansion in one turn state object does not affect another", () => {
  const turn1 = toggleTurnAnswerExpansion({
    layout: "horizontal",
    answerId: "a1",
    ...INITIAL_TURN_ANSWER_EXPANSION,
  });
  const turn2 = { ...INITIAL_TURN_ANSWER_EXPANSION };
  assert.equal(turn1.allAnswersExpanded, true);
  assert.equal(turn2.allAnswersExpanded, false);
  assert.equal(
    isTurnAnswerExpanded({
      layout: "horizontal",
      answerId: "a1",
      hasVerdict: true,
      ...turn2,
    }),
    false,
  );
});

test("without a verdict, answers stay expanded (streaming / pre-verdict)", () => {
  assert.equal(
    isTurnAnswerExpanded({
      layout: "horizontal",
      answerId: "a1",
      hasVerdict: false,
      expandedAnswerId: null,
      allAnswersExpanded: false,
    }),
    true,
  );
  assert.equal(
    isTurnAnswerExpanded({
      layout: "vertical",
      answerId: "a1",
      hasVerdict: false,
      expandedAnswerId: null,
      allAnswersExpanded: false,
    }),
    true,
  );
});

test("layout switch resets expansion to collapsed", () => {
  const afterHorizontalExpand = toggleTurnAnswerExpansion({
    layout: "horizontal",
    answerId: "a1",
    ...INITIAL_TURN_ANSWER_EXPANSION,
  });
  assert.equal(afterHorizontalExpand.allAnswersExpanded, true);
  const reset = resetTurnAnswerExpansionForLayoutChange();
  assert.deepEqual(reset, INITIAL_TURN_ANSWER_EXPANSION);
  assert.equal(
    isTurnAnswerExpanded({
      layout: "horizontal",
      answerId: "a1",
      hasVerdict: true,
      ...reset,
    }),
    false,
  );
  assert.equal(
    isTurnAnswerExpanded({
      layout: "vertical",
      answerId: "a1",
      hasVerdict: true,
      ...reset,
    }),
    false,
  );
});

test("Vertical → Horizontal and Horizontal → Vertical both start collapsed", () => {
  const fromVertical = toggleTurnAnswerExpansion({
    layout: "vertical",
    answerId: "a1",
    ...INITIAL_TURN_ANSWER_EXPANSION,
  });
  assert.equal(fromVertical.expandedAnswerId, "a1");
  const intoHorizontal = resetTurnAnswerExpansionForLayoutChange();
  assert.equal(intoHorizontal.allAnswersExpanded, false);
  assert.equal(intoHorizontal.expandedAnswerId, null);

  const fromHorizontal = toggleTurnAnswerExpansion({
    layout: "horizontal",
    answerId: "a1",
    ...INITIAL_TURN_ANSWER_EXPANSION,
  });
  assert.equal(fromHorizontal.allAnswersExpanded, true);
  const intoVertical = resetTurnAnswerExpansionForLayoutChange();
  assert.equal(intoVertical.expandedAnswerId, null);
});

test("ExpandableAnswer shows toggle only when overflowing; exposes aria-expanded", () => {
  const src = readFileSync(join(root, "src/components/chat/ExpandableAnswer.tsx"), "utf8");
  assert.match(src, /showToggle = collapsible && overflows/);
  assert.match(src, /aria-expanded=\{expanded\}/);
  assert.doesNotMatch(src, /overflows \|\| expanded/);
});

test("chat and shared turns share useTurnAnswerExpansion; Verdict stays outside sync", () => {
  const chatSrc = readFileSync(join(root, "src/routes/chat.tsx"), "utf8");
  const sharedSrc = readFileSync(join(root, "src/routes/shared.$token.tsx"), "utf8");
  const hookSrc = readFileSync(join(root, "src/hooks/useTurnAnswerExpansion.ts"), "utf8");

  assert.match(chatSrc, /useTurnAnswerExpansion/);
  assert.match(sharedSrc, /useTurnAnswerExpansion/);
  assert.match(hookSrc, /resetTurnAnswerExpansionForLayoutChange/);

  // Verdict is not passed through ExpandableAnswer sync.
  assert.match(chatSrc, /verdictBlock/);
  assert.doesNotMatch(sharedSrc, /UserPromptBubble/);
  assert.doesNotMatch(sharedSrc, /canEditUserPrompt/);
});

test("controlled ExpandableAnswer API remains expanded + onToggle", () => {
  const src = readFileSync(join(root, "src/components/chat/ExpandableAnswer.tsx"), "utf8");
  assert.match(src, /expanded: boolean/);
  assert.match(src, /onToggle: \(\) => void/);
});
