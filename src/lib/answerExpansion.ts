import type { ChatTurnLayout } from "@/lib/chatTurnLayout";

export type TurnAnswerExpansionState = {
  /** Vertical: currently expanded answer id (null = none). Accordion-style. */
  expandedAnswerId: string | null;
  /** Horizontal: shared expand/collapse for every expandable answer in the turn. */
  allAnswersExpanded: boolean;
};

export const INITIAL_TURN_ANSWER_EXPANSION: TurnAnswerExpansionState = {
  expandedAnswerId: null,
  allAnswersExpanded: false,
};

/**
 * Whether a council answer body should render expanded.
 * Without a verdict, answers stay fully open (existing streaming/pre-verdict behavior).
 */
export function isTurnAnswerExpanded(input: {
  layout: ChatTurnLayout;
  answerId: string;
  hasVerdict: boolean;
  expandedAnswerId: string | null;
  allAnswersExpanded: boolean;
}): boolean {
  if (!input.hasVerdict) return true;
  if (input.layout === "horizontal") return input.allAnswersExpanded;
  return input.expandedAnswerId === input.answerId;
}

/**
 * Next expansion state after the user toggles one answer control.
 * Vertical: independent (single-id accordion, same as before).
 * Horizontal: flip the shared turn-level flag (does not touch other turns).
 */
export function toggleTurnAnswerExpansion(input: {
  layout: ChatTurnLayout;
  answerId: string;
  expandedAnswerId: string | null;
  allAnswersExpanded: boolean;
}): TurnAnswerExpansionState {
  if (input.layout === "horizontal") {
    return {
      expandedAnswerId: null,
      allAnswersExpanded: !input.allAnswersExpanded,
    };
  }
  return {
    expandedAnswerId: input.expandedAnswerId === input.answerId ? null : input.answerId,
    allAnswersExpanded: false,
  };
}

/**
 * Predictable reset when switching Vertical ↔ Horizontal.
 * Always starts the new mode fully collapsed (per answer / shared flag).
 */
export function resetTurnAnswerExpansionForLayoutChange(): TurnAnswerExpansionState {
  return { ...INITIAL_TURN_ANSWER_EXPANSION };
}

/** Pure check: expanding answer A in turn state must not imply answer B is expanded (Vertical). */
export function verticalExpansionIsIndependent(
  expandedAnswerId: string | null,
  answerA: string,
  answerB: string,
): boolean {
  if (answerA === answerB) return true;
  const aOpen = expandedAnswerId === answerA;
  const bOpen = expandedAnswerId === answerB;
  return !(aOpen && bOpen);
}
