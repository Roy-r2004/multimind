import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Check, Copy, HelpCircle, Loader2, Save, Send, Sparkles, User, Wand2 } from "lucide-react";
import { Modal } from "@/components/Modal";
import { SavePromptDialog } from "@/components/chat/SavePromptDialog";
import { VoiceRecorderButton } from "@/components/chat/VoiceRecorderButton";
import { api } from "@/lib/api";
import type { ApiPromptBuilderContextUsage, ApiTranscriptionResponse } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";
import {
  applyManualPromptEdit,
  applyPromptBuilderClarification,
  applyPromptBuilderSuccess,
  beginPromptBuilderSend,
  clearPersistedPromptBuilderSession,
  createPromptBuilderSession,
  openPromptBuilderSession,
  originalPromptClipboardText,
  persistPromptBuilderSession,
  startNewPromptBuilderSession,
  promptBuilderMessagesForApi,
  promptBuilderStorageKey,
  type PromptBuilderSession,
} from "@/lib/promptBuilderSession";
import { cn } from "@/lib/utils";

const CONTEXT_LIMIT_MESSAGE =
  "Context limit reached. Your complete Prompt Builder history is still saved. Nothing was deleted. Start a new Builder session or use a model with a larger context window.";

const GENERATE_WITHOUT_ANSWERING_TEXT =
  "Please generate the best prompt you can without further clarification.";

type MobileTab = "conversation" | "final";

function sessionStateLabel(session: PromptBuilderSession, loading: boolean): string {
  if (loading) return "Generating…";
  if (session.latestPrompt !== null) return "Prompt ready";
  if (session.messages.length > 0) return "Awaiting input";
  return "New session";
}

export function PromptBuilderModal({
  open,
  onClose,
  onUse,
  modelSetId,
  sessionIdentity,
  initialComposerText = "",
  getComposerText,
  voiceDisabled = false,
  onVoiceRecordingStateChange,
}: {
  open: boolean;
  onClose: () => void;
  onUse: (text: string) => void;
  modelSetId: string;
  sessionIdentity: string;
  initialComposerText?: string;
  getComposerText: () => string;
  voiceDisabled?: boolean;
  onVoiceRecordingStateChange?: (active: boolean) => void;
}) {
  const [session, setSession] = useState<PromptBuilderSession>(() => createPromptBuilderSession());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [contextUsage, setContextUsage] = useState<ApiPromptBuilderContextUsage | null>(null);
  const [originalCopied, setOriginalCopied] = useState(false);
  const [finalCopied, setFinalCopied] = useState(false);
  const [mobileTab, setMobileTab] = useState<MobileTab>("conversation");
  const [showNewSessionConfirm, setShowNewSessionConfirm] = useState(false);
  const [showSave, setShowSave] = useState(false);
  const { authHeaders } = useAuth();
  const auth = authHeaders();
  const authToken = auth?.token;
  const authOrgId = auth?.orgId;
  const initialComposerTextRef = useRef(initialComposerText);
  const modelSetIdRef = useRef(modelSetId);
  initialComposerTextRef.current = initialComposerText;
  modelSetIdRef.current = modelSetId;
  const storageKey = useMemo(
    () => promptBuilderStorageKey(authOrgId ?? "anonymous", sessionIdentity),
    [authOrgId, sessionIdentity],
  );
  const loadedKey = useRef<string | null>(null);
  const hydratingKey = useRef<string | null>(null);
  const originalCopiedTimerRef = useRef<number | null>(null);
  const finalCopiedTimerRef = useRef<number | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  /** Bumped on every hydrate (open/identity change) and every New Session, so a
   * late response from a superseded lifecycle can never be applied. */
  const generationRef = useRef(0);
  const contextRequestRef = useRef(0);
  const promptRevisionRef = useRef(session.promptRevision);

  useEffect(() => {
    promptRevisionRef.current = session.promptRevision;
  }, [session.promptRevision]);

  useLayoutEffect(() => {
    if (!open) return;
    // Hydrate before paint so Original Prompt is never a blank first frame.
    // Composer/model set are captured only at open / identity change, not on later composer edits.
    hydratingKey.current = storageKey;
    generationRef.current += 1;
    const opened = openPromptBuilderSession(
      storageKey,
      initialComposerTextRef.current,
      modelSetIdRef.current,
    );
    setSession(opened);
    loadedKey.current = storageKey;
    setLoading(false);
    setError(null);
    setOriginalCopied(false);
    setFinalCopied(false);
    setMobileTab("conversation");
    setShowNewSessionConfirm(false);
    setShowSave(false);
  }, [open, storageKey]);

  useEffect(() => {
    if (hydratingKey.current === storageKey) {
      hydratingKey.current = null;
      return;
    }
    if (loadedKey.current === storageKey) persistPromptBuilderSession(storageKey, session);
  }, [session, storageKey]);

  useEffect(() => {
    return () => {
      if (originalCopiedTimerRef.current != null)
        window.clearTimeout(originalCopiedTimerRef.current);
      if (finalCopiedTimerRef.current != null) window.clearTimeout(finalCopiedTimerRef.current);
    };
  }, []);

  useEffect(() => {
    if (!open || !authToken || !session.modelSetId) {
      setContextUsage(null);
      return;
    }
    const requestAuth = { token: authToken, orgId: authOrgId };
    const messages = session.draft.trim()
      ? [...promptBuilderMessagesForApi(session), { role: "user" as const, content: session.draft }]
      : promptBuilderMessagesForApi(session);
    if (!messages.length) {
      setContextUsage(null);
      return;
    }
    const requestId = ++contextRequestRef.current;
    const requestGeneration = generationRef.current;
    const requestKey = storageKey;
    let cancelled = false;
    const stillCurrent = () =>
      !cancelled &&
      contextRequestRef.current === requestId &&
      generationRef.current === requestGeneration &&
      loadedKey.current === requestKey;
    const timer = window.setTimeout(() => {
      void api.promptBuilder
        .context(requestAuth, { model_set_id: session.modelSetId, messages })
        .then((response) => {
          if (stillCurrent()) setContextUsage(response.context_usage);
        })
        .catch(() => {
          if (stillCurrent()) setContextUsage(null);
        });
    }, 300);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [open, authToken, authOrgId, session, storageKey]);

  useEffect(() => {
    if (!open || !listRef.current) return;
    listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [open, session.messages, loading, error]);

  function handleVoiceTranscript(result: ApiTranscriptionResponse) {
    const text = result.text;
    if (!text.trim()) return;
    setSession((current) => ({
      ...current,
      draft: current.draft ? `${current.draft}\n${text}` : text,
      updatedAt: new Date().toISOString(),
    }));
    setError(null);
  }

  async function send(overrideText?: string) {
    const text = overrideText ?? session.draft;
    if (!auth || !session.modelSetId || !text.trim()) return;
    if (contextUsage && contextUsage.remaining_tokens < 0) {
      setError(CONTEXT_LIMIT_MESSAGE);
      return;
    }
    const next = beginPromptBuilderSend(session, text);
    const requestStorageKey = storageKey;
    const requestGeneration = generationRef.current;
    const requestPromptRevision = next.promptRevision;
    setSession(next);
    persistPromptBuilderSession(requestStorageKey, next);
    setLoading(true);
    setError(null);

    const stillCurrent = () =>
      loadedKey.current === requestStorageKey &&
      generationRef.current === requestGeneration &&
      promptRevisionRef.current === requestPromptRevision;

    try {
      const response = await api.promptBuilder.refine(auth, {
        model_set_id: next.modelSetId,
        messages: promptBuilderMessagesForApi(next),
      });
      if (!stillCurrent()) return;
      if (response.is_clarifying_question) {
        setSession((current) =>
          applyPromptBuilderClarification(current, response.assistant_message),
        );
      } else {
        setSession((current) =>
          applyPromptBuilderSuccess(
            current,
            response.improved_prompt ?? response.assistant_message,
          ),
        );
      }
    } catch (caught) {
      if (!stillCurrent()) return;
      const message =
        caught instanceof Error && caught.message.includes("Context limit reached")
          ? CONTEXT_LIMIT_MESSAGE
          : caught instanceof Error
            ? caught.message
            : "Could not improve prompt. Please try again.";
      setError(message);
    } finally {
      if (loadedKey.current === requestStorageKey && generationRef.current === requestGeneration) {
        setLoading(false);
      }
    }
  }

  function requestNewSession() {
    const meaningful = Boolean(
      session.originalPrompt || session.messages.length || session.draft || session.latestPrompt,
    );
    if (!meaningful) {
      performNewSession();
      return;
    }
    setShowNewSessionConfirm(true);
  }

  function performNewSession() {
    // Closing the confirm dialog first removes the button that triggered this,
    // so a repeated click cannot run this twice.
    setShowNewSessionConfirm(false);
    generationRef.current += 1;
    clearPersistedPromptBuilderSession(storageKey);
    const replacement = startNewPromptBuilderSession(modelSetId, getComposerText());
    setSession(replacement);
    setContextUsage(null);
    setError(null);
    setOriginalCopied(false);
    setFinalCopied(false);
    setLoading(false);
    setMobileTab("conversation");
  }

  async function copyOriginalPrompt() {
    try {
      await navigator.clipboard.writeText(originalPromptClipboardText(session));
      setOriginalCopied(true);
      if (originalCopiedTimerRef.current != null)
        window.clearTimeout(originalCopiedTimerRef.current);
      originalCopiedTimerRef.current = window.setTimeout(() => setOriginalCopied(false), 2000);
    } catch {
      setOriginalCopied(false);
    }
  }

  async function copyFinalPrompt() {
    if (session.latestPrompt === null) return;
    try {
      await navigator.clipboard.writeText(session.latestPrompt);
      setFinalCopied(true);
      if (finalCopiedTimerRef.current != null) window.clearTimeout(finalCopiedTimerRef.current);
      finalCopiedTimerRef.current = window.setTimeout(() => setFinalCopied(false), 2000);
    } catch {
      setFinalCopied(false);
    }
  }

  function useFinalPrompt() {
    if (session.latestPrompt === null) return;
    onUse(session.latestPrompt);
    onClose();
  }

  const hasGenerated = session.messages.length > 0;
  const lastMessage = session.messages.at(-1);
  const isClarifyingPending =
    session.latestPrompt === null && hasGenerated && lastMessage?.role === "assistant";
  const wordCount =
    session.latestPrompt && session.latestPrompt.trim()
      ? session.latestPrompt.trim().split(/\s+/).length
      : 0;
  const stateLabel = sessionStateLabel(session, loading);
  const stateTone =
    session.latestPrompt !== null && !loading
      ? "border-primary/30 bg-primary/10 text-primary"
      : "border-border bg-accent/40 text-muted-foreground";

  return (
    <>
      <Modal
        open={open}
        onClose={onClose}
        title="Prompt Builder"
        size="xl"
        className="flex h-[min(94vh,calc(100dvh-2rem))] w-[min(96vw,1500px,calc(100vw-2rem))] max-h-[min(94vh,calc(100dvh-2rem))] max-w-[min(96vw,1500px,calc(100vw-2rem))] flex-col"
        bodyClassName="flex h-full min-h-0 max-h-[none] flex-1 flex-col overflow-hidden p-0"
      >
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-2.5">
            <div className="flex items-center gap-2">
              <Wand2 className="size-4 text-primary" aria-hidden />
              <span
                className={cn("rounded-full border px-2 py-0.5 text-[11px] font-medium", stateTone)}
              >
                {stateLabel}
              </span>
            </div>
            <button
              type="button"
              onClick={requestNewSession}
              disabled={loading}
              className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-50"
            >
              New Session
            </button>
          </div>

          <div className="flex shrink-0 gap-1 border-b border-border bg-accent/5 px-3 py-2 lg:hidden">
            <button
              type="button"
              onClick={() => setMobileTab("conversation")}
              className={cn(
                "flex-1 rounded-lg px-3 py-1.5 text-xs font-medium",
                mobileTab === "conversation"
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:bg-accent",
              )}
            >
              Conversation
            </button>
            <button
              type="button"
              onClick={() => setMobileTab("final")}
              className={cn(
                "flex-1 rounded-lg px-3 py-1.5 text-xs font-medium",
                mobileTab === "final"
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:bg-accent",
              )}
            >
              Final Prompt
            </button>
          </div>

          <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 overflow-hidden p-3 lg:grid-cols-2">
            {/* Conversation panel */}
            <div
              className={cn(
                "min-h-0 flex-col gap-2.5",
                mobileTab === "conversation" ? "flex" : "hidden",
                "lg:flex",
              )}
            >
              <div className="shrink-0 rounded-xl border border-border bg-accent/10 px-2.5 py-1.5">
                <div className="flex items-center justify-between gap-2">
                  <div className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                    Original Prompt
                  </div>
                  <button
                    type="button"
                    onClick={() => void copyOriginalPrompt()}
                    className="inline-flex items-center gap-1 rounded-lg border border-border bg-background/60 px-2 py-0.5 text-[11px] font-medium text-muted-foreground hover:bg-accent hover:text-foreground"
                    data-prompt-builder-copy-original=""
                  >
                    {originalCopied ? (
                      <Check className="size-3" aria-hidden />
                    ) : (
                      <Copy className="size-3" aria-hidden />
                    )}
                    {originalCopied ? "Copied" : "Copy"}
                  </button>
                </div>
                <div className="mt-1 h-[42px] overflow-y-auto overscroll-y-contain whitespace-pre-wrap break-words text-sm leading-5">
                  {session.originalPrompt}
                </div>
              </div>

              <div
                ref={listRef}
                className="min-h-0 flex-1 basis-0 space-y-3 overflow-y-auto overscroll-y-contain rounded-xl border border-border bg-accent/10 p-3"
              >
                {session.messages.length === 0 && !loading ? (
                  <div className="flex h-full min-h-[180px] flex-col items-center justify-center gap-2 text-center text-sm text-muted-foreground">
                    <Wand2 className="size-6 text-primary" />
                    <p className="font-medium text-foreground">Let's build your prompt</p>
                    <p className="max-w-xs text-xs">
                      Describe what you want a prompt for below. Not sure it's enough detail? That's
                      fine — generate anyway and refine from there.
                    </p>
                  </div>
                ) : (
                  session.messages.map((message, index) => {
                    const isUser = message.role === "user";
                    return (
                      <div
                        key={`${message.role}-${index}`}
                        className={cn("flex", isUser ? "justify-end" : "justify-start")}
                      >
                        <div
                          className={cn(
                            "max-w-[92%] rounded-2xl px-3 py-2 text-sm",
                            isUser
                              ? "bg-primary text-primary-foreground"
                              : "border border-border bg-background",
                          )}
                        >
                          <div className="mb-1 flex items-center gap-1.5 text-[11px] opacity-80">
                            {isUser ? (
                              <>
                                <User className="size-3" /> You
                              </>
                            ) : (
                              <>
                                <Sparkles className="size-3" /> Council
                              </>
                            )}
                          </div>
                          <div className="whitespace-pre-wrap break-words">{message.content}</div>
                        </div>
                      </div>
                    );
                  })
                )}
                {loading && (
                  <div className="flex items-center gap-2 text-sm text-muted-foreground">
                    <Loader2 className="size-4 animate-spin" />
                    {hasGenerated ? "Refining your prompt..." : "Building your prompt..."}
                  </div>
                )}
              </div>

              {isClarifyingPending && !loading && (
                <button
                  type="button"
                  onClick={() => void send(GENERATE_WITHOUT_ANSWERING_TEXT)}
                  className="inline-flex shrink-0 items-center gap-1.5 self-start rounded-lg border border-border px-2.5 py-1.5 text-xs font-medium text-muted-foreground hover:bg-accent hover:text-foreground"
                >
                  <HelpCircle className="size-3.5" />
                  Generate without answering
                </button>
              )}

              {error && (
                <div className="shrink-0 rounded-xl border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
                  {error}
                </div>
              )}
              <textarea
                value={session.draft}
                onChange={(event) => {
                  setSession((current) => ({
                    ...current,
                    draft: event.target.value,
                    updatedAt: new Date().toISOString(),
                  }));
                  setError(null);
                }}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    if (!loading) void send();
                  }
                }}
                rows={3}
                disabled={loading}
                placeholder={
                  hasGenerated
                    ? "Ask for changes, or add more detail..."
                    : "Describe what you want a prompt for..."
                }
                className="min-h-[4.5rem] w-full shrink-0 rounded-xl border border-border bg-background px-3 py-3 text-sm outline-none focus:border-primary/50 disabled:opacity-60"
              />
              <div className="flex min-w-0 shrink-0 flex-wrap items-center gap-2">
                <VoiceRecorderButton
                  auth={auth}
                  disabled={voiceDisabled || loading}
                  onTranscript={handleVoiceTranscript}
                  onRecordingStateChange={onVoiceRecordingStateChange}
                />
                <button
                  type="button"
                  onClick={() => void send()}
                  disabled={
                    loading ||
                    !session.draft.trim() ||
                    Boolean(contextUsage && contextUsage.remaining_tokens < 0)
                  }
                  className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
                >
                  {loading ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <Send className="size-4" />
                  )}
                  {hasGenerated ? "Send" : "Generate Prompt"}
                </button>
                <button
                  type="button"
                  onClick={onClose}
                  disabled={loading}
                  className="ml-auto rounded-xl border border-border px-4 py-2 text-sm hover:bg-accent disabled:opacity-50"
                >
                  Cancel
                </button>
              </div>
            </div>

            {/* Final Prompt panel */}
            <div
              className={cn(
                "min-h-0 flex-col gap-2.5",
                mobileTab === "final" ? "flex" : "hidden",
                "lg:flex",
              )}
            >
              <div className="flex shrink-0 items-center justify-between gap-2">
                <div className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                  Final Prompt
                </div>
                <div className="text-[11px] text-muted-foreground">
                  {wordCount > 0 ? `${wordCount} word${wordCount === 1 ? "" : "s"}` : ""}
                </div>
              </div>
              <textarea
                value={session.latestPrompt ?? ""}
                onChange={(event) => {
                  if (session.latestPrompt === null) return;
                  setSession((current) => applyManualPromptEdit(current, event.target.value));
                }}
                disabled={loading || session.latestPrompt === null}
                placeholder="Your generated prompt will appear here once you create one."
                className="min-h-0 flex-1 basis-0 resize-none rounded-xl border border-border bg-background px-3 py-3 text-sm outline-none focus:border-primary/50 disabled:opacity-60"
              />
              <div className="flex min-w-0 shrink-0 flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={() => void copyFinalPrompt()}
                  disabled={session.latestPrompt === null}
                  className="inline-flex items-center gap-1.5 rounded-xl border border-border px-3 py-2 text-sm font-medium text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-50"
                >
                  {finalCopied ? <Check className="size-4" /> : <Copy className="size-4" />}
                  {finalCopied ? "Copied" : "Copy"}
                </button>
                <button
                  type="button"
                  onClick={() => setShowSave(true)}
                  disabled={session.latestPrompt === null}
                  className="inline-flex items-center gap-1.5 rounded-xl border border-border px-3 py-2 text-sm font-medium text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-50"
                >
                  <Save className="size-4" />
                  Save to Library
                </button>
                <button
                  type="button"
                  onClick={useFinalPrompt}
                  disabled={session.latestPrompt === null || loading}
                  className="ml-auto rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
                >
                  Use This Prompt
                </button>
              </div>
            </div>
          </div>
        </div>
      </Modal>

      <Modal
        open={showNewSessionConfirm}
        onClose={() => setShowNewSessionConfirm(false)}
        title="Start a new session?"
        size="sm"
      >
        <p className="text-sm text-muted-foreground">
          This Builder session's conversation and generated prompt will be cleared. This cannot be
          undone.
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={() => setShowNewSessionConfirm(false)}
            className="rounded-lg border border-border px-4 py-2 text-sm hover:bg-accent"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={performNewSession}
            className="rounded-lg bg-destructive px-4 py-2 text-sm font-medium text-destructive-foreground"
          >
            Start New Session
          </button>
        </div>
      </Modal>

      <SavePromptDialog
        open={showSave}
        turnId={null}
        initialPromptText={session.latestPrompt ?? ""}
        verdictPreview=""
        onClose={() => setShowSave(false)}
      />
    </>
  );
}
