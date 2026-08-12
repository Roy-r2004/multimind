/** Pure Prompt Builder mini-chat session helpers (ephemeral, frontend-owned). */

export type PromptBuilderRole = "user" | "assistant";

export type PromptBuilderChatMessage = {
  role: PromptBuilderRole;
  content: string;
};

export type PromptBuilderSession = {
  messages: PromptBuilderChatMessage[];
  draft: string;
  candidate: string | null;
  error: string | null;
  loading: boolean;
};

export function createPromptBuilderSession(seedDraft = ""): PromptBuilderSession {
  return {
    messages: [],
    draft: seedDraft,
    candidate: null,
    error: null,
    loading: false,
  };
}

export function beginPromptBuilderSend(
  session: PromptBuilderSession,
  userText: string,
): PromptBuilderSession {
  const content = userText.trim();
  if (!content) {
    return { ...session, error: "Enter a prompt to improve." };
  }
  return {
    ...session,
    messages: [...session.messages, { role: "user", content }],
    draft: "",
    error: null,
    loading: true,
  };
}

export function applyPromptBuilderSuccess(
  session: PromptBuilderSession,
  improvedPrompt: string,
): PromptBuilderSession {
  const content = improvedPrompt.trim();
  return {
    ...session,
    messages: [...session.messages, { role: "assistant", content }],
    candidate: content,
    error: null,
    loading: false,
  };
}

export function applyPromptBuilderFailure(
  session: PromptBuilderSession,
  errorMessage: string,
): PromptBuilderSession {
  return {
    ...session,
    // Keep prior candidate and messages (including the failed user turn).
    error: errorMessage,
    loading: false,
  };
}

export function clearPromptBuilderSession(): PromptBuilderSession {
  return createPromptBuilderSession("");
}

export function promptBuilderMessagesForApi(
  session: PromptBuilderSession,
): PromptBuilderChatMessage[] {
  return session.messages.map((m) => ({ role: m.role, content: m.content }));
}
