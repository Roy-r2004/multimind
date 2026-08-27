export const CHAT_BOTTOM_THRESHOLD_PX = 120;

export type ChatScrollMetrics = {
  scrollTop: number;
  scrollHeight: number;
  clientHeight: number;
};

export function distanceFromChatBottom({
  scrollTop,
  scrollHeight,
  clientHeight,
}: ChatScrollMetrics): number {
  return Math.max(0, scrollHeight - scrollTop - clientHeight);
}

export function isChatNearBottom(
  metrics: ChatScrollMetrics,
  threshold = CHAT_BOTTOM_THRESHOLD_PX,
): boolean {
  return distanceFromChatBottom(metrics) <= threshold;
}

export function shouldShowScrollToLatest(
  metrics: ChatScrollMetrics,
  threshold = CHAT_BOTTOM_THRESHOLD_PX,
): boolean {
  return !isChatNearBottom(metrics, threshold);
}

/** Scroll a target into the center of a scrollable chat thread container. */
export function scrollThreadToElement(
  thread: HTMLElement,
  target: HTMLElement,
  behavior: ScrollBehavior = "smooth",
): void {
  const threadRect = thread.getBoundingClientRect();
  const targetRect = target.getBoundingClientRect();
  const offset = targetRect.top - threadRect.top - thread.clientHeight / 2 + targetRect.height / 2;
  const nextTop = Math.max(0, thread.scrollTop + offset);
  thread.scrollTo({ top: nextTop, behavior });
}

export function findVerdictSynthesisElement(
  verdictId: string | null | undefined,
  turnId: string | null | undefined,
): HTMLElement | null {
  if (verdictId) {
    const verdict = document.getElementById(`verdict-${verdictId}`);
    if (verdict) return verdict;
  }
  if (turnId) {
    const turn = document.getElementById(`turn-${turnId}`);
    if (turn) {
      const synthesis = turn.querySelector(
        '[data-verdict-synthesis="true"], [id^="verdict-"]',
      ) as HTMLElement | null;
      if (synthesis) return synthesis;
      return turn;
    }
  }
  return null;
}
