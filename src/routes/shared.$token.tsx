import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { ChevronDown, Copy, ExternalLink, Gavel, Loader2 } from "lucide-react";
import { BrandLogo } from "@/components/BrandLogo";
import { MessageContent } from "@/components/chat/MessageContent";
import { ExpandableAnswer } from "@/components/chat/ExpandableAnswer";
import { api } from "@/lib/api";
import type { ApiSharedChat } from "@/lib/api/types";
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
        <div className="mx-auto flex h-14 max-w-4xl items-center justify-between px-6">
          <Link to="/" className="flex items-center gap-2 font-display font-semibold">
            <BrandLogo className="size-7" />
            MultiAI
          </Link>
          <div className="flex items-center gap-2">
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
  const [answersCollapsed, setAnswersCollapsed] = useState(false);
  const [expandedAnswerId, setExpandedAnswerId] = useState<string | null>(null);
  const canCollapseAnswers = Boolean(turn.verdict);
  const hasVerdict = Boolean(turn.verdict);

  const councilRail = !answersCollapsed ? (
    <aside className="space-y-3">
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
      <div className="space-y-2.5">
        {turn.model_answers.map((a) => {
          const expanded = expandedAnswerId === a.model_id || !hasVerdict;
          return (
            <div key={a.model_id} className="rounded-2xl border border-border bg-card p-3.5">
              <div className="flex items-center gap-2 text-sm">
                <span
                  className="size-2 shrink-0 rounded-full"
                  style={{ background: modelColor(a.model_id) }}
                />
                <span className="min-w-0 flex-1 truncate font-medium">{a.model_name}</span>
                {a.confidence != null && (
                  <span className="shrink-0 text-xs text-primary">{a.confidence}%</span>
                )}
              </div>
              <ExpandableAnswer
                collapsible={hasVerdict}
                expanded={expanded}
                onToggle={() =>
                  setExpandedAnswerId((current) =>
                    current === a.model_id ? null : a.model_id,
                  )
                }
                className="mt-3"
              >
                <MessageContent compact>{a.text ?? "-"}</MessageContent>
              </ExpandableAnswer>
            </div>
          );
        })}
      </div>
    </aside>
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
    <div className="rounded-2xl border border-primary/30 bg-primary/5 p-5">
      <div className="flex items-center gap-2">
        <span className="grid size-7 place-items-center rounded-lg bg-primary text-primary-foreground">
          <Gavel className="size-3.5" />
        </span>
        <div className="font-medium">Verdict AI</div>
        <span className="rounded-full bg-primary/15 px-2 py-0.5 text-xs text-primary">
          {turn.verdict.strategy}
        </span>
      </div>
      <div className="mt-3">
        <MessageContent>{turn.verdict.text}</MessageContent>
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

      {hasVerdict ? (
        <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(17rem,22rem)]">
          {verdictBlock}
          {councilRail}
        </div>
      ) : (
        councilRail
      )}
    </div>
  );
}
