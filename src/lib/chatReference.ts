/** One-shot "continue from previous chat" composer selection. */
export type ChatReferencePick = {
  chatId: string;
  title: string;
};

export const MAX_CHAT_REFERENCES = 2;

/** Fields to send on createTurn when a reference is selected. Handoff is built server-side. */
export function createTurnReferenceFields(
  refs: ChatReferencePick[] | ChatReferencePick | null | undefined,
): { referenced_chat_id?: string; referenced_chat_ids?: string[] } {
  const picks = Array.isArray(refs) ? refs : refs ? [refs] : [];
  const chatIds = picks.map((ref) => ref.chatId.trim()).filter(Boolean);
  if (chatIds.length === 0) return {};
  if (chatIds.length === 1) return { referenced_chat_id: chatIds[0] };
  return { referenced_chat_ids: chatIds.slice(0, MAX_CHAT_REFERENCES) };
}

export function toggleChatReference(
  refs: ChatReferencePick[],
  ref: ChatReferencePick,
): ChatReferencePick[] {
  if (refs.some((item) => item.chatId === ref.chatId)) {
    return refs.filter((item) => item.chatId !== ref.chatId);
  }
  if (refs.length >= MAX_CHAT_REFERENCES) return refs;
  return [...refs, ref];
}

/** After a successful createTurn, clear the composer reference chip. */
export function shouldClearReferenceAfterSend(createTurnSucceeded: boolean): boolean {
  return createTurnSucceeded;
}
