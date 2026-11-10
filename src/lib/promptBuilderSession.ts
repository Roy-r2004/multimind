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
  /**
   * Bumped by every change to latestPrompt (AI success or manual edit). A send()
   * captures this before calling the API and only applies the result if it still
   * matches on return, so a late AI response can never clobber a newer manual edit.
   */
  promptRevision: number;
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
    promptRevision: 0,
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
    promptRevision: session.promptRevision + 1,
    updatedAt: new Date().toISOString(),
  };
}

/** A clarifying question from the council: shown in the thread, but it is not a
 * final prompt — latestPrompt and promptRevision are left untouched. */
export function applyPromptBuilderClarification(
  session: PromptBuilderSession,
  question: string,
): PromptBuilderSession {
  return {
    ...session,
    messages: [...session.messages, { role: "assistant", content: question }],
    updatedAt: new Date().toISOString(),
  };
}

/** A manual edit to the Final Prompt panel. Becomes the new latestPrompt and is
 * written back into the most recent assistant message so later refinements build
 * on the edited text, not the stale AI output it replaced. */
export function applyManualPromptEdit(
  session: PromptBuilderSession,
  text: string,
): PromptBuilderSession {
  const messages = [...session.messages];
  for (let index = messages.length - 1; index >= 0; index--) {
    if (messages[index].role === "assistant") {
      messages[index] = { ...messages[index], content: text };
      break;
    }
  }
  return {
    ...session,
    messages,
    latestPrompt: text,
    promptRevision: session.promptRevision + 1,
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
      (candidate.promptRevision !== undefined && typeof candidate.promptRevision !== "number") ||
      candidate.messages.some(
        (message) =>
          !message ||
          (message.role !== "user" && message.role !== "assistant") ||
          typeof message.content !== "string",
      )
    )
      return null;
    // Sessions persisted before promptRevision existed default to 0.
    return { ...candidate, promptRevision: candidate.promptRevision ?? 0 } as PromptBuilderSession;
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

export function startNewPromptBuilderSession(
  modelSetId: string,
  composerText = "",
): PromptBuilderSession {
  // Snapshot the main chat composer only. An empty composer stays an intentional
  // empty session so a later reopen cannot invent a prompt. A non-empty composer
  // seeds originalPrompt and draft the same way a first open does, without sending.
  if (composerText === "") {
    return {
      ...createPromptBuilderSession("", modelSetId),
      intentionalEmpty: true,
    };
  }
  return createPromptBuilderSession(composerText, modelSetId);
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

/**
 * Hand off current Chat Council composer text into an already-open Builder
 * session's draft. Only fills an empty draft, so unsent Builder work is never
 * discarded, and never reintroduces text already sent as a user message in
 * this session, so reopening with unchanged composer text doesn't keep
 * re-offering (and risking a duplicate send of) already-consumed text.
 */
export function mergeComposerHandoff(
  session: PromptBuilderSession,
  composerText: string,
): PromptBuilderSession {
  if (!composerText) return session;
  if (session.draft !== "") return session;
  const alreadyConsumed = session.messages.some(
    (message) => message.role === "user" && message.content === composerText,
  );
  if (alreadyConsumed) return session;
  return { ...session, draft: composerText, updatedAt: new Date().toISOString() };
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
  const handedOff = mergeComposerHandoff(session, currentComposerText);
  persistPromptBuilderSession(key, handedOff);
  return handedOff;
}

export function clearPersistedPromptBuilderSession(key: string): void {
  if (typeof window !== "undefined") window.localStorage.removeItem(key);
}
