import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import { ArrowRight, Plus, Check, Sparkles } from "lucide-react";
import { MODEL_SETS, modelById } from "@/lib/mock";

export const Route = createFileRoute("/onboarding")({
  head: () => ({ meta: [{ title: "Choose a Model Set — MultiAI" }] }),
  component: Onboarding,
});

function Onboarding() {
  const [picked, setPicked] = useState("balanced");
  return (
    <div className="min-h-screen bg-background">
      <header className="border-b border-border">
        <div className="mx-auto flex h-14 max-w-5xl items-center justify-between px-6">
          <Link to="/" className="flex items-center gap-2 font-display font-semibold">
            <span className="grid size-7 place-items-center rounded-lg bg-primary text-primary-foreground"><Sparkles className="size-4" /></span>
            MultiAI
          </Link>
          <div className="text-xs text-muted-foreground">Step 1 of 2</div>
        </div>
      </header>
      <div className="mx-auto max-w-5xl px-6 py-12">
        <div className="max-w-2xl">
          <div className="text-xs uppercase tracking-wider text-primary">Welcome, Sara</div>
          <h1 className="mt-2 text-3xl font-semibold md:text-4xl">Pick a Model Set to start with</h1>
          <p className="mt-3 text-muted-foreground">
            A <strong>Model Set</strong> is a bundle of AI models that answer your question together, plus a Verdict AI that decides on the final answer. You can change it anytime.
          </p>
        </div>

        <div className="mt-8 grid gap-4 sm:grid-cols-2">
          {MODEL_SETS.map((s) => {
            const active = picked === s.id;
            return (
              <button
                key={s.id}
                onClick={() => setPicked(s.id)}
                className={`group rounded-2xl border bg-card p-5 text-left transition ${active ? "border-primary ring-2 ring-primary/30" : "border-border hover:border-foreground/20"}`}
              >
                <div className="flex items-start justify-between">
                  <div>
                    <div className="font-medium">{s.name}</div>
                    <div className="mt-1 text-sm text-muted-foreground">{s.description}</div>
                  </div>
                  <div className={`grid size-6 place-items-center rounded-full ${active ? "bg-primary text-primary-foreground" : "bg-muted text-transparent"}`}>
                    <Check className="size-3.5" />
                  </div>
                </div>
                <div className="mt-4 flex flex-wrap gap-2">
                  {s.models.map((id) => {
                    const m = modelById(id);
                    return (
                      <span key={id} className="inline-flex items-center gap-1.5 rounded-full border border-border bg-background px-2.5 py-1 text-xs">
                        <span className="size-1.5 rounded-full" style={{ background: m.color }} /> {m.name}
                      </span>
                    );
                  })}
                </div>
                <div className="mt-3 text-xs text-muted-foreground">Verdict strategy: <span className="text-foreground">{s.strategy}</span></div>
              </button>
            );
          })}
        </div>

        <div className="mt-8 flex flex-wrap items-center gap-3">
          <Link to="/model-sets/new" className="inline-flex items-center gap-2 rounded-xl border border-border bg-card px-4 py-2.5 text-sm font-medium hover:bg-accent">
            <Plus className="size-4" /> Create custom Model Set
          </Link>
          <Link to="/chat" className="ml-auto inline-flex items-center gap-2 rounded-xl bg-primary px-5 py-2.5 text-sm font-medium text-primary-foreground hover:opacity-90">
            Continue to chat <ArrowRight className="size-4" />
          </Link>
        </div>
      </div>
    </div>
  );
}
