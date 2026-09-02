export const CHAT_BOTTOM_THRESHOLD_PX = 120;
export const CHAT_SCROLL_RESTORE_SETTLE_MS = 220;
export const CHAT_TURN_ELEMENT_ID_PREFIX = "turn-";

export type ChatScrollMetrics = {
  scrollTop: number;
  scrollHeight: number;
  clientHeight: number;
};

export type ChatScrollSnapshot = {
  nearBottom: boolean;
  turnId: string | null;
  offsetPx: number;
  scrollTop: number;
};

export type ChatTurnLayoutRect = {
  turnId: string;
  top: number;
  height: number;
};

export type ChatScrollEnterPlan =
  | { action: "pin-latest" }
  | { action: "restore"; snapshot: ChatScrollSnapshot }
  | { action: "explicit-turn"; turnId: string };

export type ChatScrollMemory = ChatScrollMetrics & {
  turnId: string | null;
  offsetPx: number;
};

export type ChatScrollSession = {
  pin: boolean;
  restored: boolean;
  pendingRestore: boolean;
  latestCalls: number;
};

const snapshotsByChatId = new Map<string, ChatScrollSnapshot>();
const lastMemoryByChatId = new Map<string, ChatScrollMemory>();

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

export function chatTurnElementId(turnId: string): string {
  return `${CHAT_TURN_ELEMENT_ID_PREFIX}${turnId}`;
}

export function turnIdFromElementId(elementId: string): string | null {
  if (!elementId.startsWith(CHAT_TURN_ELEMENT_ID_PREFIX)) return null;
  const turnId = elementId.slice(CHAT_TURN_ELEMENT_ID_PREFIX.length);
  return turnId || null;
}

export function setChatScrollSnapshot(chatId: string, snapshot: ChatScrollSnapshot): void {
  snapshotsByChatId.set(chatId, snapshot);
}

export function getChatScrollSnapshot(chatId: string): ChatScrollSnapshot | undefined {
  return snapshotsByChatId.get(chatId);
}

export function resetChatScrollSnapshots(): void {
  snapshotsByChatId.clear();
  lastMemoryByChatId.clear();
}

export function isLiveChatThread(thread: {
  isConnected: boolean;
  clientHeight: number;
  scrollHeight: number;
}): boolean {
  return thread.isConnected && thread.clientHeight > 0 && thread.scrollHeight > 0;
}

export function snapshotFromScrollMemory(memory: ChatScrollMemory): ChatScrollSnapshot {
  return {
    nearBottom: isChatNearBottom(memory),
    turnId: memory.turnId,
    offsetPx: memory.offsetPx,
    scrollTop: memory.scrollTop,
  };
}

export function rememberChatScrollMemory(chatId: string, memory: ChatScrollMemory): void {
  lastMemoryByChatId.set(chatId, memory);
  setChatScrollSnapshot(chatId, snapshotFromScrollMemory(memory));
}

export function getChatScrollMemory(chatId: string): ChatScrollMemory | undefined {
  return lastMemoryByChatId.get(chatId);
}

/** Persist a snapshot from a live thread, or from the last live metrics if the node is detached. */
export function persistChatScrollSnapshot(
  chatId: string,
  thread: HTMLElement | null,
): ChatScrollSnapshot | undefined {
  if (thread && isLiveChatThread(thread)) {
    const snapshot = captureChatScrollSnapshot(thread);
    rememberChatScrollMemory(chatId, {
      scrollTop: snapshot.scrollTop,
      scrollHeight: thread.scrollHeight,
      clientHeight: thread.clientHeight,
      turnId: snapshot.turnId,
      offsetPx: snapshot.offsetPx,
    });
    return snapshot;
  }
  const memory = lastMemoryByChatId.get(chatId);
  if (memory) {
    const snapshot = snapshotFromScrollMemory(memory);
    setChatScrollSnapshot(chatId, snapshot);
    return snapshot;
  }
  return snapshotsByChatId.get(chatId);
}

export function chatScrollSessionOnEnter(
  snapshot: ChatScrollSnapshot | undefined,
  explicitTurnId: string | null = null,
): ChatScrollSession {
  const plan = planChatScrollEnter(snapshot, explicitTurnId);
  if (plan.action === "pin-latest") {
    return { pin: true, restored: true, pendingRestore: false, latestCalls: 1 };
  }
  if (plan.action === "explicit-turn") {
    return { pin: false, restored: true, pendingRestore: false, latestCalls: 0 };
  }
  return { pin: false, restored: false, pendingRestore: true, latestCalls: 0 };
}

export function chatScrollSessionOnTurnsReady(session: ChatScrollSession): ChatScrollSession {
  if (!session.pendingRestore) return session;
  return {
    pin: false,
    restored: true,
    pendingRestore: false,
    latestCalls: session.latestCalls,
  };
}

export function chatScrollSessionOnSend(session: ChatScrollSession): ChatScrollSession {
  return { ...session, pin: true, latestCalls: session.latestCalls + 1 };
}

export function shouldRestoreReadingPosition(
  snapshot: ChatScrollSnapshot | undefined,
): snapshot is ChatScrollSnapshot {
  return Boolean(snapshot && !snapshot.nearBottom);
}

export function planChatScrollEnter(
  snapshot: ChatScrollSnapshot | undefined,
  explicitTurnId: string | null | undefined,
): ChatScrollEnterPlan {
  const turnId = explicitTurnId?.trim() || null;
  if (turnId) return { action: "explicit-turn", turnId };
  if (shouldRestoreReadingPosition(snapshot)) return { action: "restore", snapshot };
  return { action: "pin-latest" };
}

export function shouldPinToBottomForPlan(plan: ChatScrollEnterPlan): boolean {
  return plan.action === "pin-latest";
}

/** First intersecting turn in document order, with offset from the thread viewport top. */
export function pickFirstVisibleTurnAnchor(
  threadTop: number,
  threadHeight: number,
  turns: ChatTurnLayoutRect[],
): { turnId: string; offsetPx: number } | null {
  const threadBottom = threadTop + threadHeight;
  for (const turn of turns) {
    if (turn.height <= 0) continue;
    const bottom = turn.top + turn.height;
    if (bottom > threadTop && turn.top < threadBottom) {
      return { turnId: turn.turnId, offsetPx: turn.top - threadTop };
    }
  }
  return null;
}

export function captureChatScrollSnapshotFromLayout(
  metrics: ChatScrollMetrics,
  threadTop: number,
  turns: ChatTurnLayoutRect[],
): ChatScrollSnapshot {
  const nearBottom = isChatNearBottom(metrics);
  const anchor = pickFirstVisibleTurnAnchor(threadTop, metrics.clientHeight, turns);
  return {
    nearBottom,
    turnId: anchor?.turnId ?? null,
    offsetPx: anchor?.offsetPx ?? 0,
    scrollTop: metrics.scrollTop,
  };
}

export function captureChatScrollSnapshot(thread: HTMLElement): ChatScrollSnapshot {
  const threadRect = thread.getBoundingClientRect();
  const metrics: ChatScrollMetrics = {
    scrollTop: thread.scrollTop,
    scrollHeight: thread.scrollHeight,
    clientHeight: thread.clientHeight,
  };
  const turns: ChatTurnLayoutRect[] = [];
  for (const node of thread.querySelectorAll(`[id^="${CHAT_TURN_ELEMENT_ID_PREFIX}"]`)) {
    if (!(node instanceof HTMLElement)) continue;
    const turnId = turnIdFromElementId(node.id);
    if (!turnId) continue;
    const rect = node.getBoundingClientRect();
    turns.push({ turnId, top: rect.top, height: rect.height });
  }
  return captureChatScrollSnapshotFromLayout(metrics, threadRect.top, turns);
}

export function nextScrollTopForRestore(
  currentScrollTop: number,
  snapshot: ChatScrollSnapshot,
  turnOffsetFromThreadTop: number | null,
): number {
  if (snapshot.nearBottom) return currentScrollTop;
  if (turnOffsetFromThreadTop === null) return Math.max(0, snapshot.scrollTop);
  return Math.max(0, currentScrollTop + turnOffsetFromThreadTop - snapshot.offsetPx);
}

export function restoreChatScrollPosition(thread: HTMLElement, snapshot: ChatScrollSnapshot): void {
  if (snapshot.nearBottom) return;
  const turn = snapshot.turnId ? document.getElementById(chatTurnElementId(snapshot.turnId)) : null;
  if (turn) {
    const threadRect = thread.getBoundingClientRect();
    const turnRect = turn.getBoundingClientRect();
    thread.scrollTop = nextScrollTopForRestore(
      thread.scrollTop,
      snapshot,
      turnRect.top - threadRect.top,
    );
    return;
  }
  thread.scrollTop = nextScrollTopForRestore(thread.scrollTop, snapshot, null);
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
