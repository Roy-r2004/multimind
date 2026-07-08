import { useEffect, useRef, useState } from "react";
import { Loader2, Send, Sparkles } from "lucide-react";
import { useNavigate } from "@tanstack/react-router";
import { Modal } from "@/components/Modal";
import { MessageContent } from "@/components/chat/MessageContent";
import { api } from "@/lib/api";
import type { ApiDiscussMessage } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

const OPENING =
  "I read the verdict and your pushback matters. Walk me through what feels wrong — what did the council get wrong, and what would you do instead?";

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
  const [messages, setMessages] = useState<ApiDiscussMessage[]>([]);
  const [input, setInput] = useState("");
  const [lessonId, setLessonId] = useState<string | null>(null);
  const [canFinalize, setCanFinalize] = useState(false);
  const [loading, setLoading] = useState(false);
  const [starting, setStarting] = useState(false);
  const [finalizing, setFinalizing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const onDiscussStartRef = useRef(onDiscussStart);
  const startedForTurnRef = useRef<string | null>(null);

  useEffect(() => {
    onDiscussStartRef.current = onDiscussStart;
  }, [onDiscussStart]);

  useEffect(() => {
    if (!open) {
      startedForTurnRef.current = null;
      return;
    }
    if (startedForTurnRef.current === turnId) return;

    const auth = authHeaders();
    if (!auth) {
      setError("Sign in to discuss the verdict.");
      return;
    }

    startedForTurnRef.current = turnId;
    setError(null);
    setInput("");
    setMessages([{ role: "Chafic", content: OPENING }]);
    setLessonId(null);
    setCanFinalize(false);
    setStarting(true);

    let cancelled = false;
    void api.lessons
      .discussStart(auth, turnId)
      .then((res) => {
        if (cancelled) return;
        const next =
          res.messages.length > 0 ? res.messages : [{ role: "Chafic", content: OPENING }];
        setMessages(next);
        setLessonId(res.lesson_id);
        setCanFinalize(res.can_finalize);
        onDiscussStartRef.current(res.lesson_id);
      })
      .catch((e) => {
        if (cancelled) return;
        startedForTurnRef.current = null;
        setError(e instanceof Error ? e.message : "Could not start discussion");
      })
      .finally(() => {
        if (!cancelled) setStarting(false);
      });

    return () => {
      cancelled = true;
    };
  }, [open, turnId, authHeaders]);

  useEffect(() => {
    if (!open) return;
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    const timer = window.setTimeout(() => inputRef.current?.focus(), 80);
    return () => window.clearTimeout(timer);
  }, [open, messages]);

  async function sendMessage() {
    const text = input.trim();
    if (!text || loading || starting || finalizing) return;
    const auth = authHeaders();
    if (!auth) return;

    setInput("");
    setError(null);
    setLoading(true);
    setMessages((prev) => [...prev, { role: userName, content: text }]);

    try {
      const res = await api.lessons.discuss(auth, turnId, text);
      setMessages(res.messages);
      setLessonId(res.lesson_id);
      setCanFinalize(res.can_finalize);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to send message");
      setMessages((prev) => {
        const copy = [...prev];
        const idx = copy.findLastIndex((m) => m.role === userName && m.content === text);
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
          {starting && messages.length === 0 ? (
            <div className="flex items-center justify-center py-12 text-sm text-muted-foreground">
              <Loader2 className="mr-2 size-4 animate-spin" /> Opening discussion…
            </div>
          ) : (
            messages.map((m, i) => {
              const isChafic = m.role === "Chafic";
              return (
                <div
                  key={`${i}-${m.role}`}
                  className={cn("flex", isChafic ? "justify-start" : "justify-end")}
                >
                  <div
                    className={cn(
                      "max-w-[88%] rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed",
                      isChafic
                        ? "rounded-bl-sm border border-border bg-card text-foreground"
                        : "rounded-br-sm bg-primary/90 text-primary-foreground",
                    )}
                  >
                    {isChafic && (
                      <div className="mb-1 flex items-center gap-1.5 text-xs font-semibold text-primary">
                        <Sparkles className="size-3" /> Chafic
                      </div>
                    )}
                    {isChafic ? (
                      <MessageContent compact>{m.content}</MessageContent>
                    ) : (
                      <p className="whitespace-pre-wrap">{m.content}</p>
                    )}
                  </div>
                </div>
              );
            })
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
              disabled={loading || starting || finalizing}
              className="min-h-[44px] flex-1 resize-none rounded-xl border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary disabled:opacity-50"
            />
            <button
              type="button"
              onClick={() => void sendMessage()}
              disabled={loading || starting || finalizing || !input.trim()}
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
          disabled={!canFinalize || finalizing || loading || starting}
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
      {lessonId && canFinalize && (
        <p className="mt-2 text-center text-xs text-muted-foreground">
          When you&apos;re ready, finish to lock in what you discussed and update your brain.
        </p>
      )}
    </Modal>
  );
}
