import { createFileRoute, Link } from "@tanstack/react-router";
import { Copy, ExternalLink, Gavel, Sparkles } from "lucide-react";
import { MODEL_SETS, SAMPLE_ANSWERS, VERDICT, modelById } from "@/lib/mock";

export const Route = createFileRoute("/shared")({
  head: () => ({ meta: [{ title: "Shared chat — MultiAI" }] }),
  component: Shared,
});

function Shared() {
  const set = MODEL_SETS[0];
  return (
    <div className="min-h-screen bg-background">
      <header className="border-b border-border">
        <div className="mx-auto flex h-14 max-w-4xl items-center justify-between px-6">
          <Link to="/" className="flex items-center gap-2 font-display font-semibold">
            <span className="grid size-7 place-items-center rounded-lg bg-primary text-primary-foreground">
              <Sparkles className="size-4" />
            </span>
            MultiAI
          </Link>
          <div className="flex items-center gap-2">
            <button className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm hover:bg-accent">
              <Copy className="size-3.5" /> Copy link
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

      <div className="mx-auto max-w-4xl px-6 py-10">
        <div className="text-xs text-muted-foreground">
          Shared by Sara K. · Balanced Set · Read-only
        </div>
        <h1 className="mt-1 text-xl font-semibold">Best framework for a fast SaaS landing page?</h1>

        <div className="mt-6 flex justify-end">
          <div className="max-w-[80%] rounded-2xl rounded-br-md bg-primary px-4 py-3 text-sm text-primary-foreground">
            What's the best framework for a fast SaaS landing page in 2026?
          </div>
        </div>

        <div className="mt-6 grid gap-3 md:grid-cols-3">
          {set.models.map((id, i) => {
            const m = modelById(id);
            const a = SAMPLE_ANSWERS[i] ?? SAMPLE_ANSWERS[0];
            return (
              <div key={id} className="rounded-2xl border border-border bg-card p-4">
                <div className="flex items-center gap-2 text-sm">
                  <span className="size-2 rounded-full" style={{ background: m.color }} />
                  <span className="font-medium">{m.name}</span>
                  <span className="ml-auto text-xs text-muted-foreground">{a.confidence}%</span>
                </div>
                <p className="mt-3 text-sm leading-relaxed">{a.text}</p>
              </div>
            );
          })}
        </div>

        <div className="mt-6 rounded-2xl border border-primary/30 bg-primary/5 p-5">
          <div className="flex items-center gap-2">
            <span className="grid size-7 place-items-center rounded-lg bg-primary text-primary-foreground">
              <Gavel className="size-3.5" />
            </span>
            <div className="font-medium">Verdict AI</div>
            <span className="rounded-full bg-primary/15 px-2 py-0.5 text-xs text-primary">
              {VERDICT.strategy}
            </span>
          </div>
          <p className="mt-3 text-sm leading-relaxed">{VERDICT.text}</p>
        </div>
      </div>
    </div>
  );
}
