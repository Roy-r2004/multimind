/** Lossless, frontend-owned Prompt Builder session helpers. */

export type PromptBuilderRole = "user" | "assistant";
export type PromptBuilderChatMessage = { role: PromptBuilderRole; content: string };

export type PromptBuilderSession = {
  version: 1;
  originalPrompt: string;
  messages: PromptBuilderChatMessage[];
  draft: string;
  latestPrompt: string | null;
  modelSetId: string;
  updatedAt: string;
  /** Set only by New Session so an intentional empty original is not repaired from the composer. */
  intentionalEmpty?: boolean;
};

export function createPromptBuilderSession(
  originalPrompt = "",
  modelSetId = "",
): PromptBuilderSession {
  return {
    version: 1,
    originalPrompt,
    messages: [],
    draft: originalPrompt,
    latestPrompt: null,
    modelSetId,
    updatedAt: new Date().toISOString(),
  };
}

export function beginPromptBuilderSend(
  session: PromptBuilderSession,
  userText: string,
): PromptBuilderSession {
  if (!userText.trim()) return session;
  const captureOriginalPrompt = session.originalPrompt === "" && session.messages.length === 0;
  return {
    ...session,
    originalPrompt: captureOriginalPrompt ? userText : session.originalPrompt,
    messages: [...session.messages, { role: "user", content: userText }],
    draft: "",
    updatedAt: new Date().toISOString(),
    ...(captureOriginalPrompt ? { intentionalEmpty: false } : {}),
  };
}

export function applyPromptBuilderSuccess(
  session: PromptBuilderSession,
  improvedPrompt: string,
): PromptBuilderSession {
  return {
    ...session,
    messages: [...session.messages, { role: "assistant", content: improvedPrompt }],
    latestPrompt: improvedPrompt,
    updatedAt: new Date().toISOString(),
  };
}

export function promptBuilderMessagesForApi(
  session: PromptBuilderSession,
): PromptBuilderChatMessage[] {
  return session.messages.map((message) => ({ ...message }));
}

export function promptBuilderStorageKey(orgId: string, identity: string): string {
  return `multimind:prompt-builder:${orgId}:${identity}`;
}

export function loadPromptBuilderSession(key: string): PromptBuilderSession | null {
  if (typeof window === "undefined") return null;
  try {
    const value = JSON.parse(window.localStorage.getItem(key) ?? "null") as unknown;
    if (!value || typeof value !== "object") return null;
    const candidate = value as Partial<PromptBuilderSession>;
    if (
      candidate.version !== 1 ||
      typeof candidate.originalPrompt !== "string" ||
      !Array.isArray(candidate.messages) ||
      typeof candidate.draft !== "string" ||
      !(typeof candidate.latestPrompt === "string" || candidate.latestPrompt === null) ||
      typeof candidate.modelSetId !== "string" ||
      typeof candidate.updatedAt !== "string" ||
      (candidate.intentionalEmpty !== undefined &&
        typeof candidate.intentionalEmpty !== "boolean") ||
      candidate.messages.some(
        (message) =>
          !message ||
          (message.role !== "user" && message.role !== "assistant") ||
          typeof message.content !== "string",
      )
    )
      return null;
    return candidate as PromptBuilderSession;
  } catch {
    return null;
  }
}

export function savePromptBuilderSession(key: string, session: PromptBuilderSession): void {
  if (typeof window !== "undefined") window.localStorage.setItem(key, JSON.stringify(session));
}

export function hasMeaningfulPromptBuilderHistory(session: PromptBuilderSession): boolean {
  return session.messages.length > 0 || session.latestPrompt !== null;
}

export function isEmptyPromptBuilderShell(session: PromptBuilderSession): boolean {
  return (
    session.originalPrompt === "" &&
    session.draft === "" &&
    !hasMeaningfulPromptBuilderHistory(session)
  );
}

export function originalPromptClipboardText(session: PromptBuilderSession): string {
  return session.originalPrompt;
}

export function startNewPromptBuilderSession(modelSetId: string): PromptBuilderSession {
  return {
    ...createPromptBuilderSession("", modelSetId),
    intentionalEmpty: true,
  };
}

export function resolvePromptBuilderSession(
  saved: PromptBuilderSession | null,
  currentComposerText: string,
  modelSetId: string,
): PromptBuilderSession {
  const repairPoisonedEmptySession =
    saved !== null &&
    saved.originalPrompt === "" &&
    saved.intentionalEmpty !== true &&
    !hasMeaningfulPromptBuilderHistory(saved) &&
    currentComposerText !== "";
  if (saved && !repairPoisonedEmptySession) return saved;
  return createPromptBuilderSession(currentComposerText, modelSetId);
}

export function persistPromptBuilderSession(key: string, session: PromptBuilderSession): void {
  if (isEmptyPromptBuilderShell(session) && session.intentionalEmpty !== true) {
    clearPersistedPromptBuilderSession(key);
    return;
  }
  savePromptBuilderSession(key, session);
}

export function openPromptBuilderSession(
  key: string,
  currentComposerText: string,
  modelSetId: string,
): PromptBuilderSession {
  const session = resolvePromptBuilderSession(
    loadPromptBuilderSession(key),
    currentComposerText,
    modelSetId,
  );
  persistPromptBuilderSession(key, session);
  return session;
}

export function clearPersistedPromptBuilderSession(key: string): void {
  if (typeof window !== "undefined") window.localStorage.removeItem(key);
}
