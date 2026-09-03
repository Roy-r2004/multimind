import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Check, Copy, Loader2, Send, Sparkles, User, Wand2 } from "lucide-react";
import { Modal } from "@/components/Modal";
import { VoiceRecorderButton } from "@/components/chat/VoiceRecorderButton";
import { api } from "@/lib/api";
import type { ApiPromptBuilderContextUsage, ApiTranscriptionResponse } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";
import {
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

function compactTokens(value: number): string {
  return value >= 1000 ? `${(value / 1000).toFixed(1)}K` : String(value);
}

export function PromptBuilderModal({
  open,
  onClose,
  onUse,
  modelSetId,
  sessionIdentity,
  initialComposerText = "",
  voiceDisabled = false,
  onVoiceRecordingStateChange,
}: {
  open: boolean;
  onClose: () => void;
  onUse: (text: string) => void;
  modelSetId: string;
  sessionIdentity: string;
  initialComposerText?: string;
  voiceDisabled?: boolean;
  onVoiceRecordingStateChange?: (active: boolean) => void;
}) {
  const [session, setSession] = useState<PromptBuilderSession>(() => createPromptBuilderSession());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [contextUsage, setContextUsage] = useState<ApiPromptBuilderContextUsage | null>(null);
  const [lastActualUsage, setLastActualUsage] = useState<ApiPromptBuilderContextUsage | null>(null);
  const [originalCopied, setOriginalCopied] = useState(false);
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
  const copiedTimerRef = useRef<number | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);

  useLayoutEffect(() => {
    if (!open) return;
    // Hydrate before paint so Original Prompt is never a blank first frame.
    // Composer/model set are captured only at open / identity change, not on later composer edits.
    hydratingKey.current = storageKey;
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
      if (copiedTimerRef.current != null) window.clearTimeout(copiedTimerRef.current);
    };
  }, []);

  useEffect(() => {
    if (!open || !authToken || !session.modelSetId) return;
    const requestAuth = { token: authToken, orgId: authOrgId };
    const messages = session.draft.trim()
      ? [...promptBuilderMessagesForApi(session), { role: "user" as const, content: session.draft }]
      : promptBuilderMessagesForApi(session);
    if (!messages.length) {
      setContextUsage(null);
      return;
    }
    const timer = window.setTimeout(() => {
      void api.promptBuilder
        .context(requestAuth, { model_set_id: session.modelSetId, messages })
        .then((response) => setContextUsage(response.context_usage))
        .catch(() => setContextUsage(null));
    }, 300);
    return () => window.clearTimeout(timer);
  }, [open, authToken, authOrgId, session]);

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

  async function send() {
    if (!auth || !session.modelSetId || !session.draft.trim()) return;
    if (contextUsage && contextUsage.remaining_tokens < 0) {
      setError(CONTEXT_LIMIT_MESSAGE);
      return;
    }
    const next = beginPromptBuilderSend(session, session.draft);
    const requestStorageKey = storageKey;
    setSession(next);
    persistPromptBuilderSession(requestStorageKey, next);
    setLoading(true);
    setError(null);
    try {
      const response = await api.promptBuilder.refine(auth, {
        model_set_id: next.modelSetId,
        messages: promptBuilderMessagesForApi(next),
      });
      if (loadedKey.current !== requestStorageKey) return;
      setSession(applyPromptBuilderSuccess(next, response.improved_prompt));
      setLastActualUsage(response.context_usage);
    } catch (caught) {
      if (loadedKey.current !== requestStorageKey) return;
      const message =
        caught instanceof Error && caught.message.includes("Context limit reached")
          ? CONTEXT_LIMIT_MESSAGE
          : caught instanceof Error
            ? caught.message
            : "Could not improve prompt. Please try again.";
      setError(message);
    } finally {
      setLoading(false);
    }
  }

  function reset() {
    const meaningful = Boolean(
      session.originalPrompt || session.messages.length || session.draft || session.latestPrompt,
    );
    if (
      meaningful &&
      !window.confirm(
        "Start a new Prompt Builder session? The saved Builder history will be cleared.",
      )
    )
      return;
    clearPersistedPromptBuilderSession(storageKey);
    const replacement = startNewPromptBuilderSession(modelSetId);
    setSession(replacement);
    setContextUsage(null);
    setLastActualUsage(null);
    setError(null);
    setOriginalCopied(false);
  }

  async function copyOriginalPrompt() {
    try {
      await navigator.clipboard.writeText(originalPromptClipboardText(session));
      setOriginalCopied(true);
      if (copiedTimerRef.current != null) window.clearTimeout(copiedTimerRef.current);
      copiedTimerRef.current = window.setTimeout(() => setOriginalCopied(false), 2000);
    } catch {
      setOriginalCopied(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Prompt Builder"
      size="lg"
      className="flex h-[min(92vh,900px)] max-h-[min(92vh,900px)] max-w-[min(94vw,960px)] flex-col"
      bodyClassName="flex min-h-0 max-h-none flex-1 flex-col overflow-hidden p-5"
    >
      <div className="flex min-h-0 flex-1 flex-col gap-4">
        <div className="shrink-0 rounded-xl border border-border bg-accent/10 p-3">
          <div className="flex items-center justify-between gap-2">
            <div className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
              Original Prompt
            </div>
            <button
              type="button"
              onClick={() => void copyOriginalPrompt()}
              className="inline-flex items-center gap-1 rounded-lg border border-border bg-background/60 px-2 py-1 text-[11px] font-medium text-muted-foreground hover:bg-accent hover:text-foreground"
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
          <div className="mt-1 max-h-[min(20vh,10rem)] min-h-[1.25rem] overflow-y-auto whitespace-pre-wrap break-words text-sm">
            {session.originalPrompt}
          </div>
        </div>

        {contextUsage && (
          <div className="shrink-0 rounded-xl border border-border px-3 py-2 text-xs text-muted-foreground">
            <div>
              Context ({contextUsage.is_estimate ? "estimated" : "actual"}):{" "}
              {compactTokens(
                contextUsage.actual_input_tokens ?? contextUsage.estimated_input_tokens,
              )}{" "}
              / {compactTokens(contextUsage.context_limit)}
            </div>
            <div>
              {compactTokens(Math.max(0, contextUsage.remaining_tokens))} remaining after{" "}
              {compactTokens(contextUsage.reserved_output_tokens)} output reservation
            </div>
            <div>
              Limiting model: {contextUsage.limiting_model_name} ({contextUsage.limiting_call})
            </div>
            {lastActualUsage?.actual_input_tokens != null && (
              <div>
                Last request actual input: {compactTokens(lastActualUsage.actual_input_tokens)} (
                {lastActualUsage.limiting_model_name}, {lastActualUsage.limiting_call})
              </div>
            )}
          </div>
        )}

        <div
          ref={listRef}
          className="min-h-0 flex-1 space-y-3 overflow-y-auto rounded-xl border border-border bg-accent/10 p-3"
        >
          {session.messages.length === 0 && !loading ? (
            <div className="flex h-full min-h-[180px] items-center justify-center gap-2 text-sm text-muted-foreground">
              <Wand2 className="size-5" />
              Send the original prompt or add refinement instructions.
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
              Council is improving your prompt...
            </div>
          )}
        </div>

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
          rows={4}
          disabled={loading}
          placeholder="Refine this prompt..."
          className="min-h-[5.5rem] w-full shrink-0 rounded-xl border border-border bg-background px-3 py-3 text-sm outline-none focus:border-primary/50 disabled:opacity-60"
        />
        <div className="flex min-w-0 shrink-0 flex-wrap items-center gap-2 py-2">
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
            {loading ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
            Send
          </button>
          <button
            type="button"
            onClick={() => {
              if (session.latestPrompt !== null) onUse(session.latestPrompt);
              onClose();
            }}
            disabled={session.latestPrompt === null || loading}
            className="rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
          >
            Use This Prompt
          </button>
          <button
            type="button"
            onClick={onClose}
            disabled={loading}
            className="rounded-xl border border-border px-4 py-2 text-sm hover:bg-accent disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={reset}
            disabled={loading}
            className="ml-auto rounded-xl border border-destructive/40 px-3 py-2 text-sm text-destructive disabled:opacity-50"
          >
            New Session
          </button>
        </div>
      </div>
    </Modal>
  );
}
