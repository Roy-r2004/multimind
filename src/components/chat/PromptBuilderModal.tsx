import { useEffect, useRef, useState } from "react";
import { Loader2, Send, Sparkles, User, Wand2 } from "lucide-react";
import { Modal } from "@/components/Modal";
import { VoiceRecorderButton } from "@/components/chat/VoiceRecorderButton";
import { api } from "@/lib/api";
import type { ApiTranscriptionResponse } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";
import {
  applyPromptBuilderFailure,
  applyPromptBuilderSuccess,
  beginPromptBuilderSend,
  clearPromptBuilderSession,
  createPromptBuilderSession,
  promptBuilderMessagesForApi,
  type PromptBuilderSession,
} from "@/lib/promptBuilderSession";
import { cn } from "@/lib/utils";

export function PromptBuilderModal({
  open,
  onClose,
  onUse,
  modelSetId,
  initialComposerText = "",
  voiceDisabled = false,
  onVoiceRecordingStateChange,
}: {
  open: boolean;
  onClose: () => void;
  onUse: (text: string) => void;
  modelSetId: string;
  initialComposerText?: string;
  voiceDisabled?: boolean;
  onVoiceRecordingStateChange?: (active: boolean) => void;
}) {
  const [session, setSession] = useState<PromptBuilderSession>(() =>
    createPromptBuilderSession(""),
  );
  const { authHeaders } = useAuth();
  const voiceAuth = authHeaders();
  const wasOpen = useRef(false);
  const listRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (open && !wasOpen.current) {
      setSession(createPromptBuilderSession(initialComposerText));
    }
    if (!open && wasOpen.current) {
      setSession(clearPromptBuilderSession());
    }
    wasOpen.current = open;
  }, [open, initialComposerText]);

  useEffect(() => {
    if (!open) return;
    const el = listRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [open, session.messages, session.loading, session.error]);

  function closeWithoutUse() {
    setSession(clearPromptBuilderSession());
    onClose();
  }

  function handleVoiceTranscript(result: ApiTranscriptionResponse) {
    const text = result.text.trim();
    if (!text) return;
    setSession((current) => {
      const existing = current.draft.trimEnd();
      return {
        ...current,
        draft: existing ? `${existing}\n${text}` : text,
        error: null,
      };
    });
  }

  async function send() {
    const auth = authHeaders();
    if (!auth) {
      setSession((current) =>
        applyPromptBuilderFailure(current, "Could not improve prompt. Please try again."),
      );
      return;
    }
    if (!modelSetId) {
      setSession((current) =>
        applyPromptBuilderFailure(current, "Select a model set before using Prompt Builder."),
      );
      return;
    }

    const draft = session.draft;
    const next = beginPromptBuilderSend(session, draft);
    if (next.error && !next.loading) {
      setSession(next);
      return;
    }
    setSession(next);

    try {
      const response = await api.promptBuilder.refine(auth, {
        model_set_id: modelSetId,
        messages: promptBuilderMessagesForApi(next),
      });
      const improved = response.improved_prompt || response.assistant_message;
      setSession((current) => applyPromptBuilderSuccess(current, improved));
    } catch {
      setSession((current) =>
        applyPromptBuilderFailure(
          current,
          "Could not improve prompt. Please try again.",
        ),
      );
    }
  }

  function usePrompt() {
    const text = session.candidate?.trim();
    if (!text) return;
    onUse(text);
    setSession(clearPromptBuilderSession());
    onClose();
  }

  return (
    <Modal open={open} onClose={closeWithoutUse} title="Prompt Builder" size="lg">
      <div className="flex max-h-[min(70vh,640px)] flex-col gap-4">
        <p className="text-sm text-muted-foreground">
          Refine a prompt with the council. This mini-chat is isolated from your current chat.
        </p>

        <div
          ref={listRef}
          className="min-h-[220px] flex-1 space-y-3 overflow-y-auto rounded-xl border border-border bg-accent/10 p-3"
        >
          {session.messages.length === 0 && !session.loading ? (
            <div className="flex h-full min-h-[180px] flex-col items-center justify-center gap-2 px-4 text-center text-sm text-muted-foreground">
              <Wand2 className="size-5 opacity-70" />
              <p>Describe what you want improved. The council will return one refined prompt.</p>
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
                    <div
                      className={cn(
                        "mb-1 flex items-center gap-1.5 text-[11px] font-medium",
                        isUser ? "text-primary-foreground/80" : "text-muted-foreground",
                      )}
                    >
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
          {session.loading && (
            <div className="flex items-center gap-2 rounded-xl border border-border bg-background px-3 py-2 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" />
              Council is improving your prompt...
            </div>
          )}
        </div>

        {session.error && (
          <div className="rounded-xl border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
            {session.error}
          </div>
        )}

        <div className="space-y-2">
          <textarea
            value={session.draft}
            onChange={(e) =>
              setSession((current) => ({
                ...current,
                draft: e.target.value,
                error: null,
              }))
            }
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                if (!session.loading) void send();
              }
            }}
            rows={3}
            disabled={session.loading}
            placeholder="Improve this prompt: …"
            className="w-full rounded-xl border border-border bg-background px-3 py-3 text-sm outline-none focus:border-primary/50 disabled:opacity-60"
          />
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <VoiceRecorderButton
              auth={voiceAuth}
              disabled={voiceDisabled || session.loading}
              onTranscript={handleVoiceTranscript}
              onRecordingStateChange={onVoiceRecordingStateChange}
            />
            <button
              type="button"
              onClick={() => void send()}
              disabled={session.loading || !session.draft.trim()}
              className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
            >
              {session.loading ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Send className="size-4" />
              )}
              Send
            </button>
            <button
              type="button"
              onClick={usePrompt}
              disabled={!session.candidate || session.loading}
              className="rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
            >
              Use Prompt
            </button>
            <button
              type="button"
              onClick={closeWithoutUse}
              disabled={session.loading}
              className="rounded-xl border border-border px-4 py-2 text-sm hover:bg-accent disabled:opacity-50"
            >
              Cancel
            </button>
          </div>
        </div>
      </div>
    </Modal>
  );
}
