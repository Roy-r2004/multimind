export type ChatTurnLayout = "vertical" | "horizontal";

/** Browser preference key for chat-turn answer card layout. */
export const CHAT_TURN_LAYOUT_KEY = "multimind.chatTurnLayout";

export const DEFAULT_CHAT_TURN_LAYOUT: ChatTurnLayout = "vertical";

/** Same-tab notification so `useSyncExternalStore` subscribers refresh. */
export const CHAT_TURN_LAYOUT_EVENT = "multimind:chat-turn-layout";

/** In-tab override so UI updates even when localStorage write fails. */
let sessionLayout: ChatTurnLayout | null = null;

export function isChatTurnLayout(value: unknown): value is ChatTurnLayout {
  return value === "vertical" || value === "horizontal";
}

/** Coerce a stored/raw value to a valid layout; invalid → Vertical. */
export function parseChatTurnLayout(value: unknown): ChatTurnLayout {
  if (typeof value !== "string") return DEFAULT_CHAT_TURN_LAYOUT;
  const trimmed = value.trim().toLowerCase();
  return isChatTurnLayout(trimmed) ? trimmed : DEFAULT_CHAT_TURN_LAYOUT;
}

function readStoredChatTurnLayout(): ChatTurnLayout {
  try {
    if (typeof window === "undefined") return DEFAULT_CHAT_TURN_LAYOUT;
    return parseChatTurnLayout(window.localStorage.getItem(CHAT_TURN_LAYOUT_KEY));
  } catch {
    return DEFAULT_CHAT_TURN_LAYOUT;
  }
}

/**
 * Current layout preference. Prefers this-tab session value (after a set),
 * otherwise reads localStorage safely.
 */
export function readChatTurnLayout(): ChatTurnLayout {
  if (sessionLayout !== null) return sessionLayout;
  return readStoredChatTurnLayout();
}

/** Clear session override so the next read comes from storage (other-tab sync). */
export function clearChatTurnLayoutSession(): void {
  sessionLayout = null;
}

export function writeChatTurnLayout(layout: ChatTurnLayout): boolean {
  if (!isChatTurnLayout(layout)) return false;
  sessionLayout = layout;
  let persisted = false;
  try {
    if (typeof window !== "undefined") {
      window.localStorage.setItem(CHAT_TURN_LAYOUT_KEY, layout);
      persisted = true;
    }
  } catch {
    persisted = false;
  }
  try {
    if (typeof window !== "undefined") {
      window.dispatchEvent(new Event(CHAT_TURN_LAYOUT_EVENT));
    }
  } catch {
    /* ignore */
  }
  return persisted;
}

/**
 * CSS classes for the model-answer card container.
 * Vertical: full-width stack. Horizontal: responsive auto-fit grid (no hard-coded
 * four-column count). Verdict must stay outside this container.
 */
export function chatAnswerCardsClassName(layout: ChatTurnLayout): string {
  if (layout === "horizontal") {
    return [
      "grid min-w-0 gap-4",
      // Narrow → 1 col; grows with width (≈2 tablet, up to ≈4 on wide chat column).
      "[grid-template-columns:repeat(auto-fit,minmax(min(100%,15.5rem),1fr))]",
      "[&>*]:min-w-0",
    ].join(" ");
  }
  return "grid min-w-0 grid-cols-1 gap-4 [&>*]:min-w-0";
}

export function subscribeChatTurnLayout(onStoreChange: () => void): () => void {
  if (typeof window === "undefined") return () => {};

  const onCustom = () => onStoreChange();
  const onStorage = (event: StorageEvent) => {
    if (event.key !== null && event.key !== CHAT_TURN_LAYOUT_KEY) return;
    clearChatTurnLayoutSession();
    onStoreChange();
  };

  window.addEventListener(CHAT_TURN_LAYOUT_EVENT, onCustom);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(CHAT_TURN_LAYOUT_EVENT, onCustom);
    window.removeEventListener("storage", onStorage);
  };
}

/** Test helper — reset module session state between cases. */
export function __resetChatTurnLayoutSessionForTests(): void {
  sessionLayout = null;
}
