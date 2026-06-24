import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { Copy, ExternalLink, Gavel, Loader2, ShieldCheck } from "lucide-react";
import { BrandLogo } from "@/components/BrandLogo";
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
              to="/signup"
              className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground"
            >
              <ExternalLink className="size-3.5" /> Open in my account
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
          <div key={turn.id} className="space-y-6">
            <div className="flex justify-end">
              <div className="max-w-[85%] rounded-2xl rounded-br-md bg-primary px-4 py-3 text-sm text-primary-foreground">
                {turn.user_message}
              </div>
            </div>

            <div className="grid gap-3 md:grid-cols-3">
              {turn.model_answers.map((a) => (
                  <div key={a.model_id} className="rounded-2xl border border-border bg-card p-4">
                    <div className="flex items-center gap-2 text-sm">
                      <span className="size-2 rounded-full" style={{ background: modelColor(a.model_id) }} />
                      <span className="font-medium">{a.model_name}</span>
                      {a.confidence != null && (
                        <span className="ml-auto text-xs text-muted-foreground">
                          {a.confidence}%
                        </span>
                      )}
                    </div>
                    <p className="mt-3 text-sm leading-relaxed">{a.text ?? "—"}</p>
                  </div>
              ))}
            </div>

            {turn.verdict && (
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
                <p className="mt-3 text-sm leading-relaxed">{turn.verdict.text}</p>
              </div>
            )}

            {turn.decision_insurance && (
              <div className="rounded-2xl border border-amber-500/20 bg-amber-50/70 p-5 dark:bg-amber-950/10">
                <div className="flex items-center gap-2">
                  <ShieldCheck className="size-4 text-amber-600" />
                  <div className="font-medium">Decision Insurance</div>
                </div>
                <p className="mt-3 text-sm">{turn.decision_insurance.mitigation_plan}</p>
              </div>
            )}
          </div>
        ))}

        {!lastTurn && (
          <p className="text-sm text-muted-foreground">This chat has no messages yet.</p>
        )}
      </div>
    </div>
  );
}
