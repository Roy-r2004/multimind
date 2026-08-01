import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { ChevronDown, Copy, ExternalLink, Gavel, Loader2 } from "lucide-react";
import { BrandLogo } from "@/components/BrandLogo";
import { MessageContent } from "@/components/chat/MessageContent";
import { ExpandableAnswer } from "@/components/chat/ExpandableAnswer";
import { VerdictCopyButton } from "@/components/chat/VerdictCopyButton";
import { ChatTurnLayoutToggle } from "@/components/chat/ChatTurnLayoutToggle";
import { api } from "@/lib/api";
import type { ApiSharedChat } from "@/lib/api/types";
import { chatAnswerCardsClassName } from "@/lib/chatTurnLayout";
import { useChatTurnLayout } from "@/hooks/useChatTurnLayout";
import { useTurnAnswerExpansion } from "@/hooks/useTurnAnswerExpansion";
import { modelColor } from "@/lib/models";

export const Route = createFileRoute("/shared/$token")({
  head: () => ({ meta: [{ title: "Shared chat — MultiAI" }] }),
  component: SharedPage,
});

function SharedPage() {
  const { token } = Route.useParams();
  const [data, setData] = useState<ApiSharedChat | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [turnLayout, setTurnLayout] = useChatTurnLayout();

  useEffect(() => {
    void api.share
      .get(token)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load"));
  }, [token]);

  if (error) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <p className="text-sm text-destructive">{error}</p>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const lastTurn = data.turns[data.turns.length - 1];

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b border-border">
        <div className="mx-auto flex h-14 max-w-4xl items-center justify-between gap-3 px-6">
          <Link to="/" className="flex items-center gap-2 font-display font-semibold">
            <BrandLogo className="size-7" />
            MultiAI
          </Link>
          <div className="flex items-center gap-2">
            <ChatTurnLayoutToggle value={turnLayout} onChange={setTurnLayout} />
            <button
              onClick={() => {
                void navigator.clipboard.writeText(window.location.href);
                setCopied(true);
                setTimeout(() => setCopied(false), 2000);
              }}
              className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm hover:bg-accent"
            >
              <Copy className="size-3.5" /> {copied ? "Copied!" : "Copy link"}
            </button>
            <Link
              to="/login"
              className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground"
            >
              <ExternalLink className="size-3.5" /> Log in
            </Link>
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-4xl space-y-10 px-6 py-10">
        <div>
          <div className="text-xs text-muted-foreground">
            Shared by {data.shared_by} · {data.model_set_name} · Read-only
          </div>
          <h1 className="mt-1 text-xl font-semibold">{data.title}</h1>
        </div>

        {data.turns.map((turn) => (
          <SharedTurn key={turn.id} turn={turn} />
        ))}

        {!lastTurn && (
          <p className="text-sm text-muted-foreground">This chat has no messages yet.</p>
        )}
      </div>
    </div>
  );
}

function SharedTurn({ turn }: { turn: ApiSharedChat["turns"][number] }) {
  const [layout] = useChatTurnLayout();
  const { isExpanded, toggle: toggleAnswerExpansion } = useTurnAnswerExpansion(layout);
  const [answersCollapsed, setAnswersCollapsed] = useState(false);
  const canCollapseAnswers = Boolean(turn.verdict);
  const hasVerdict = Boolean(turn.verdict);

  const responseCards = !answersCollapsed ? (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.22em] text-primary">
            AI Council
          </p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {turn.model_answers.length} models · {turn.model_answers.length} perspectives
          </p>
        </div>
        {canCollapseAnswers ? (
          <button
            type="button"
            onClick={() => setAnswersCollapsed(true)}
            className="rounded-lg border border-border bg-card/70 px-2 py-1 text-[11px] font-medium text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            Hide
          </button>
        ) : null}
      </div>
      <div
        className={chatAnswerCardsClassName(layout)}
        data-chat-answer-layout={layout}
        data-testid="shared-answer-layout"
      >
        {turn.model_answers.map((a) => {
          const expanded = isExpanded(a.model_id, hasVerdict);
          const color = modelColor(a.model_id);
          return (
            <div
              key={a.model_id}
              className="min-w-0 w-full overflow-hidden rounded-2xl border border-border border-l-[3px] bg-card p-4 shadow-[0_1px_0_oklch(1_0_0/0.8)_inset,0_8px_28px_oklch(0.45_0.04_240/0.06)] sm:p-5"
              style={{ borderLeftColor: color }}
            >
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span
                  className="size-2.5 shrink-0 rounded-full"
                  style={{ background: color }}
                />
                <span className="min-w-0 flex-1 font-semibold leading-tight">{a.model_name}</span>
                {a.confidence != null && (
                  <span className="shrink-0 text-xs text-primary">{a.confidence}%</span>
                )}
              </div>
              <div className="mt-3 border-t border-border/60 pt-3 sm:mt-4 sm:pt-4">
                <ExpandableAnswer
                  collapsible={hasVerdict}
                  expanded={expanded}
                  onToggle={() => toggleAnswerExpansion(a.model_id)}
                >
                  <MessageContent>{a.text ?? "-"}</MessageContent>
                </ExpandableAnswer>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  ) : (
    <button
      type="button"
      onClick={() => setAnswersCollapsed(false)}
      className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-card/70 px-3 py-1.5 text-xs font-medium text-muted-foreground transition hover:bg-accent hover:text-foreground"
    >
      <ChevronDown className="size-3.5 -rotate-90" />
      Show AI council ({turn.model_answers.length})
    </button>
  );

  const verdictBlock = turn.verdict ? (
    <div className="w-full pt-2" data-testid="shared-verdict">
      <div className="rounded-2xl border-2 border-primary/30 bg-primary/[0.04] p-5 shadow-[0_1px_0_oklch(1_0_0/0.9)_inset,0_16px_44px_oklch(0.55_0.1_240/0.14)] sm:p-6">
        <div className="flex flex-wrap items-start gap-3 border-b border-primary/15 pb-4">
          <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-primary text-primary-foreground shadow-sm">
            <Gavel className="size-5" />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-lg font-semibold tracking-tight sm:text-xl">Verdict</h3>
              <span className="rounded-full bg-primary/15 px-2 py-0.5 text-xs font-medium text-primary">
                {turn.verdict.strategy}
              </span>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <VerdictCopyButton text={turn.verdict.text} />
          </div>
        </div>
        <div className="mt-5">
          <MessageContent>{turn.verdict.text}</MessageContent>
        </div>
      </div>
    </div>
  ) : null;

  return (
    <div className="space-y-6">
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-br-md bg-primary px-4 py-3 text-sm text-primary-foreground">
          <p className="whitespace-pre-wrap leading-relaxed">{turn.user_message}</p>
        </div>
      </div>

      <div className="space-y-4">
        {responseCards}
        {verdictBlock}
      </div>
    </div>
  );
}
