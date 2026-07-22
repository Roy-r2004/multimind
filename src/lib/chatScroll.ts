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
