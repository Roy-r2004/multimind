import { useCallback, useSyncExternalStore } from "react";
import {
  DEFAULT_CHAT_TURN_LAYOUT,
  type ChatTurnLayout,
  isChatTurnLayout,
  readChatTurnLayout,
  subscribeChatTurnLayout,
  writeChatTurnLayout,
} from "@/lib/chatTurnLayout";

/**
 * Hydration-safe chat-turn layout preference (localStorage + same-tab sync).
 * Server / hydration snapshot is always Vertical to avoid mismatch.
 */
export function useChatTurnLayout(): [
  ChatTurnLayout,
  (layout: ChatTurnLayout) => void,
] {
  const layout = useSyncExternalStore(
    subscribeChatTurnLayout,
    readChatTurnLayout,
    () => DEFAULT_CHAT_TURN_LAYOUT,
  );

  const setLayout = useCallback((next: ChatTurnLayout) => {
    if (!isChatTurnLayout(next)) return;
    writeChatTurnLayout(next);
  }, []);

  return [layout, setLayout];
}
