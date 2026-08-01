import { useCallback, useEffect, useRef, useState } from "react";
import type { ChatTurnLayout } from "@/lib/chatTurnLayout";
import {
  INITIAL_TURN_ANSWER_EXPANSION,
  isTurnAnswerExpanded,
  resetTurnAnswerExpansionForLayoutChange,
  toggleTurnAnswerExpansion,
} from "@/lib/answerExpansion";

/**
 * Per-turn answer expansion. Vertical = independent (single-id); Horizontal = synced.
 * Resets to collapsed when layout mode changes.
 */
export function useTurnAnswerExpansion(layout: ChatTurnLayout) {
  const [state, setState] = useState(INITIAL_TURN_ANSWER_EXPANSION);
  const prevLayoutRef = useRef(layout);

  useEffect(() => {
    if (prevLayoutRef.current === layout) return;
    prevLayoutRef.current = layout;
    setState(resetTurnAnswerExpansionForLayoutChange());
  }, [layout]);

  const isExpanded = useCallback(
    (answerId: string, hasVerdict: boolean) =>
      isTurnAnswerExpanded({
        layout,
        answerId,
        hasVerdict,
        expandedAnswerId: state.expandedAnswerId,
        allAnswersExpanded: state.allAnswersExpanded,
      }),
    [layout, state.expandedAnswerId, state.allAnswersExpanded],
  );

  const toggle = useCallback(
    (answerId: string) => {
      setState((current) =>
        toggleTurnAnswerExpansion({
          layout,
          answerId,
          expandedAnswerId: current.expandedAnswerId,
          allAnswersExpanded: current.allAnswersExpanded,
        }),
      );
    },
    [layout],
  );

  return {
    isExpanded,
    toggle,
    expandedAnswerId: state.expandedAnswerId,
    allAnswersExpanded: state.allAnswersExpanded,
  };
}
