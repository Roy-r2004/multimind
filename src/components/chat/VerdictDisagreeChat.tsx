import { useEffect, useRef, useState } from "react";
import { Loader2, Send, Sparkles, User } from "lucide-react";
import { useNavigate } from "@tanstack/react-router";
import { Modal } from "@/components/Modal";
import { MessageContent } from "@/components/chat/MessageContent";
import { api } from "@/lib/api";
import type { ApiDiscussMessage } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

const OPENING =
  "I read the verdict and your pushback matters. Walk me through what feels wrong — what did the council get wrong, and what would you do instead?";

const OPENING_MARKER = "Walk me through what feels wrong";

function isAssistantRole(role: string): boolean {
  const r = role.trim().toLowerCase();
  // Do NOT treat "chafic"/"chafiq" as assistant — demo user has that name.
  return r === "assistant" || r === "ai" || r === "facilitator";
}

function looksLikeOpening(content: string, index: number): boolean {
  const text = content.trim();
  if (!text) return false;
  if (text === OPENING.trim()) return true;
  return index === 0 && text.includes(OPENING_MARKER);
}

function normalizeMessages(messages: ApiDiscussMessage[]): ApiDiscussMessage[] {
  const roleKeys = new Set(messages.map((m) => m.role.trim().toLowerCase()));
  const fullyAmbiguous =
    messages.length > 0 &&
    [...roleKeys].every((r) => r === "chafic" || r === "chafiq");

  let last: "assistant" | "user" | null = null;
  return messages.map((m, index) => {
    const r = m.role.trim().toLowerCase();
    let role: "assistant" | "user";

    if (isAssistantRole(m.role)) {
      role = "assistant";
    } else if (r === "user") {
      role = "user";
    } else if (looksLikeOpening(m.content, index)) {
      role = "assistant";
    } else if (fullyAmbiguous) {
      if (last === null) role = index === 0 ? "assistant" : "user";
      else role = last === "assistant" ? "user" : "assistant";
    } else if (r === "chafic" || r === "chafiq") {
      // Mixed transcripts: facilitator used to be labeled Chafic.
      role = "assistant";
    } else {
      role = "user";
    }

    last = role;
    return { role, content: m.content };
  });
}

function hasUserMessage(messages: ApiDiscussMessage[]): boolean {
  return messages.some((m) => {
    const r = m.role.trim().toLowerCase();
    return r === "user";
  });
}

export function VerdictDisagreeChat({
  open,
  onClose,
  turnId,
  userName,
  onDiscussStart,
  onLessonBuilt,
}: {
  open: boolean;
  onClose: () => void;
  turnId: string;
  userName: string;
  onDiscussStart: (lessonId: string) => void;
  onLessonBuilt: (lessonId: string) => void;
}) {
  const { authHeaders } = useAuth();
  const navigate = useNavigate();
  const [messages, setMessages] = useState<ApiDiscussMessage[]>([
    { role: "assistant", content: OPENING },
  ]);
  const [input, setInput] = useState("");
  const [lessonId, setLessonId] = useState<string | null>(null);
  const [canFinalize, setCanFinalize] = useState(false);
  const [loading, setLoading] = useState(false);
  const [ready, setReady] = useState(false);
  const [finalizing, setFinalizing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const onDiscussStartRef = useRef(onDiscussStart);
  const requestIdRef = useRef(0);

  useEffect(() => {
    onDiscussStartRef.current = onDiscussStart;
  }, [onDiscussStart]);

  useEffect(() => {
    if (!open) return;

    const auth = authHeaders();
    if (!auth) {
      setError("Sign in to discuss the verdict.");
      setReady(false);
      return;
    }

    const requestId = ++requestIdRef.current;
    setError(null);
    setInput("");
    setMessages([{ role: "assistant", content: OPENING }]);
    setLessonId(null);
    setCanFinalize(false);
    setReady(false);

    void (async () => {
      try {
        const liveTurn = await api.chats.getTurn(auth, turnId);
        if (requestId !== requestIdRef.current) return;
        if (!liveTurn.verdict) {
          setReady(false);
          setError("This turn has no verdict yet. Wait for the council to finish, then try again.");
          return;
        }

        const res = await api.lessons.discussStart(auth, liveTurn.id);
        if (requestId !== requestIdRef.current) return;
        const next = normalizeMessages(
          res.messages.length > 0 ? res.messages : [{ role: "assistant", content: OPENING }],
        );
        setMessages(next);
        setLessonId(res.lesson_id);
        setCanFinalize(res.can_finalize || hasUserMessage(next));
        setReady(true);
        onDiscussStartRef.current(res.lesson_id);
      } catch (e) {
        if (requestId !== requestIdRef.current) return;
        setReady(false);
        const message = e instanceof Error ? e.message : "Could not start discussion";
        if (/turn not found/i.test(message)) {
          setError(
            "This chat turn is no longer available (it may have been deleted during an API restart). Send a new question, wait for the verdict, then disagree again.",
          );
        } else {
          setError(message);
        }
      }
    })();
  }, [open, turnId, authHeaders]);

  useEffect(() => {
    if (!open) return;
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    const timer = window.setTimeout(() => inputRef.current?.focus(), 80);
    return () => window.clearTimeout(timer);
  }, [open, messages]);

  async function sendMessage() {
    const text = input.trim();
    if (!text || loading || !ready || finalizing) return;
    const auth = authHeaders();
    if (!auth) return;

    setInput("");
    setError(null);
    setLoading(true);
    setMessages((prev) => [...prev, { role: "user", content: text }]);

    try {
      const res = await api.lessons.discuss(auth, turnId, text);
      const next = normalizeMessages(res.messages);
      setMessages(next);
      setLessonId(res.lesson_id);
      setCanFinalize(res.can_finalize || hasUserMessage(next));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to send message");
      setMessages((prev) => {
        const copy = [...prev];
        const idx = copy.findLastIndex((m) => m.role === "user" && m.content === text);
        if (idx >= 0) copy.splice(idx, 1);
        return copy;
      });
    } finally {
      setLoading(false);
    }
  }

  async function finalize() {
    const auth = authHeaders();
    if (!auth) return;
    if (!hasUserMessage(messages)) {
      setError("Send at least one message explaining why you disagree first.");
      return;
    }
    setFinalizing(true);
    setError(null);
    try {
      const res = await api.lessons.discussFinalize(auth, turnId);
      onLessonBuilt(res.lesson.id);
      onClose();
      void navigate({ to: "/lessons/$id", params: { id: res.lesson.id } });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to build lesson");
    } finally {
      setFinalizing(false);
    }
  }

  const finishEnabled = (canFinalize || hasUserMessage(messages)) && ready && !loading && !finalizing;

  return (
    <Modal open={open} onClose={onClose} title="Discuss with Chafic" size="xl">
      <p className="text-sm text-muted-foreground">
        Argue your case. Chafic won&apos;t rubber-stamp you — he&apos;ll push back fairly until you
        both land on clarity, then we&apos;ll build your lesson.
      </p>

      {error && (
        <div className="mt-3 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {error}
        </div>
      )}

      <div className="mt-4 flex h-[min(52vh,420px)] flex-col rounded-xl border border-border bg-muted/20">
        <div className="flex-1 space-y-3 overflow-y-auto p-4">
          {messages.map((m, i) => {
            const isAssistant = m.role.trim().toLowerCase() === "assistant";
            return (
              <div
                key={`${i}-${m.role}-${m.content.slice(0, 24)}`}
                className={cn("flex", isAssistant ? "justify-start" : "justify-end")}
              >
                <div
                  className={cn(
                    "max-w-[88%] rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed",
                    isAssistant
                      ? "rounded-bl-sm border border-border bg-card text-foreground"
                      : "rounded-br-sm bg-primary/90 text-primary-foreground",
                  )}
                >
                  <div
                    className={cn(
                      "mb-1 flex items-center gap-1.5 text-xs font-semibold",
                      isAssistant ? "text-primary" : "text-primary-foreground/85",
                    )}
                  >
                    {isAssistant ? (
                      <>
                        <Sparkles className="size-3" /> Chafic
                      </>
                    ) : (
                      <>
                        <User className="size-3" /> You
                        {userName ? ` · ${userName}` : ""}
                      </>
                    )}
                  </div>
                  {isAssistant ? (
                    <MessageContent compact>{m.content}</MessageContent>
                  ) : (
                    <p className="whitespace-pre-wrap">{m.content}</p>
                  )}
                </div>
              </div>
            );
          })}
          {!ready && !error && (
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Loader2 className="size-3.5 animate-spin" /> Preparing discussion…
            </div>
          )}
          {loading && (
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Loader2 className="size-3.5 animate-spin" /> Chafic is thinking…
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        <div className="border-t border-border bg-background p-3">
          <div className="flex gap-2">
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void sendMessage();
                }
              }}
              rows={2}
              disabled={loading || !ready || finalizing}
              className="min-h-[44px] flex-1 resize-none rounded-xl border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary disabled:opacity-50"
            />
            <button
              type="button"
              onClick={() => void sendMessage()}
              disabled={loading || !ready || finalizing || !input.trim()}
              className="grid size-10 shrink-0 place-items-center self-end rounded-xl bg-primary text-primary-foreground disabled:opacity-50"
            >
              <Send className="size-4" />
            </button>
          </div>
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
        <button
          type="button"
          onClick={onClose}
          disabled={finalizing}
          className="rounded-xl border border-border px-4 py-2 text-sm hover:bg-accent disabled:opacity-50"
        >
          Save for later
        </button>
        <button
          type="button"
          onClick={() => void finalize()}
          disabled={!finishEnabled}
          className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
        >
          {finalizing ? (
            <>
              <Loader2 className="size-4 animate-spin" /> Building lesson…
            </>
          ) : (
            "Finish & build lesson"
          )}
        </button>
      </div>
      {!finishEnabled && ready && !error && (
        <p className="mt-2 text-center text-xs text-muted-foreground">
          Send at least one reply explaining your disagreement, then finish.
        </p>
      )}
      {finishEnabled && (
        <p className="mt-2 text-center text-xs text-muted-foreground">
          When you&apos;re ready, finish to lock in what you discussed and update your brain.
        </p>
      )}
    </Modal>
  );
}
