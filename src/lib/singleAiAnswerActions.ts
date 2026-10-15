/** Copy and Pin visibility for a turn that has no Council Verdict. */

export type SingleAiAnswerActionState = {
  showCopy: boolean;
  showPin: boolean;
  copyText: string;
  answerId: string | null;
};

export function singleAiAnswerActionState(input: {
  answerCount: number;
  hasVerdict: boolean;
  status: string;
  text?: string | null;
  answerId?: string | null;
}): SingleAiAnswerActionState {
  const copyText = (input.text ?? "").trim();
  const oneCompletedAnswer =
    input.answerCount === 1 &&
    !input.hasVerdict &&
    input.status === "completed" &&
    copyText.length > 0;
  const answerId = input.answerId?.trim() || null;
  return {
    showCopy: oneCompletedAnswer,
    showPin: oneCompletedAnswer && Boolean(answerId),
    copyText: oneCompletedAnswer ? (input.text ?? "") : "",
    answerId: oneCompletedAnswer ? answerId : null,
  };
}
