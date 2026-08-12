/** One-shot "continue from previous chat" composer selection. */
export type ChatReferencePick = {
  chatId: string;
  title: string;
};

/** Fields to send on createTurn when a reference is selected. Handoff is built server-side. */
export function createTurnReferenceFields(
  ref: ChatReferencePick | null | undefined,
): { referenced_chat_id?: string } {
  const chatId = ref?.chatId?.trim();
  if (!chatId) return {};
  return { referenced_chat_id: chatId };
}

/** After a successful createTurn, clear the composer reference chip. */
export function shouldClearReferenceAfterSend(createTurnSucceeded: boolean): boolean {
  return createTurnSucceeded;
}
