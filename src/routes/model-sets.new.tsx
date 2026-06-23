import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import { AlertCircle, Check, Gavel } from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { MODELS, STRATEGIES, type Strategy } from "@/lib/mock";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/model-sets/new")({
  head: () => ({ meta: [{ title: "Create Model Set — MultiAI" }] }),
  component: NewModelSet,
});

function NewModelSet() {
  const [name, setName] = useState("My Set");
  const [desc, setDesc] = useState("");
  const [picked, setPicked] = useState<string[]>(["gpt-4.1", "claude", "gemini"]);
  const [verdictModel, setVerdictModel] = useState("gpt-4.1");
  const [strategy, setStrategy] = useState<Strategy>("Synthesize");
  const [custom, setCustom] = useState("");

  function toggle(id: string) {
    setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  }

  return (
    <AppShell>
      <div className="mx-auto max-w-3xl px-6 py-10">
        <Link to="/model-sets" className="text-sm text-muted-foreground hover:text-foreground">
          ← Back to Model Sets
        </Link>
        <h1 className="mt-2 text-2xl font-semibold">Create Model Set</h1>

        <div className="mt-8 space-y-6">
          <Section title="Basics">
            <label className="block text-sm">
              <div className="mb-1 font-medium">Name</div>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
              />
            </label>
            <label className="block text-sm">
              <div className="mb-1 font-medium">Description</div>
              <textarea
                value={desc}
                onChange={(e) => setDesc(e.target.value)}
                rows={2}
                placeholder="What's this set good for?"
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
              />
            </label>
          </Section>

          <Section title="Models" subtitle="Pick the models that will answer in parallel.">
            <div className="grid gap-2 sm:grid-cols-2">
              {MODELS.map((m) => {
                const on = picked.includes(m.id);
                return (
                  <button
                    key={m.id}
                    onClick={() => toggle(m.id)}
                    className={cn(
                      "flex items-center gap-3 rounded-xl border p-3 text-left",
                      on ? "border-primary bg-accent/40" : "border-border hover:bg-accent",
                    )}
                  >
                    <span className="size-2.5 rounded-full" style={{ background: m.color }} />
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-medium">
                        {m.name} <span className="text-xs text-muted-foreground">· {m.vendor}</span>
                      </div>
                      <div className="text-xs text-muted-foreground">{m.blurb}</div>
                    </div>
                    <div
                      className={cn(
                        "grid size-5 place-items-center rounded-md border",
                        on ? "border-primary bg-primary text-primary-foreground" : "border-border",
                      )}
                    >
                      {on && <Check className="size-3" />}
                    </div>
                  </button>
                );
              })}
            </div>
          </Section>

          <Section title="Verdict AI" subtitle="The judge that produces the final answer.">
            <label className="block text-sm">
              <div className="mb-1 font-medium">Verdict model</div>
              <select
                value={verdictModel}
                onChange={(e) => setVerdictModel(e.target.value)}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
              >
                {MODELS.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name} — {m.vendor}
                  </option>
                ))}
              </select>
            </label>
            <div className="text-sm">
              <div className="mb-1 font-medium">Strategy</div>
              <div className="grid gap-2 sm:grid-cols-2">
                {STRATEGIES.map((s) => (
                  <button
                    key={s.name}
                    onClick={() => setStrategy(s.name)}
                    className={cn(
                      "rounded-xl border p-3 text-left",
                      strategy === s.name
                        ? "border-primary bg-accent/40"
                        : "border-border hover:bg-accent",
                    )}
                  >
                    <div className="flex items-center gap-2 text-sm font-medium">
                      <Gavel className="size-3.5 text-primary" /> {s.name}
                    </div>
                    <div className="mt-1 text-xs text-muted-foreground">{s.desc}</div>
                  </button>
                ))}
              </div>
            </div>
            <label className="block text-sm">
              <div className="mb-1 font-medium">
                Custom Verdict instructions{" "}
                <span className="font-normal text-muted-foreground">(optional)</span>
              </div>
              <textarea
                value={custom}
                onChange={(e) => setCustom(e.target.value)}
                rows={3}
                placeholder="e.g. Prefer answers backed by recent sources. Penalize hand-waving."
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
              />
            </label>
            {custom.trim() && (
              <div className="flex items-start gap-2 rounded-lg border border-warning/40 bg-warning/10 p-3 text-sm">
                <AlertCircle className="mt-0.5 size-4 text-warning" />
                <div>
                  <strong>Custom instructions override the selected strategy.</strong> The Verdict
                  AI will follow your instructions instead.
                </div>
              </div>
            )}
          </Section>

          <div className="flex justify-end gap-2">
            <Link
              to="/model-sets"
              className="rounded-xl border border-border px-4 py-2 text-sm hover:bg-accent"
            >
              Cancel
            </Link>
            <Link
              to="/model-sets"
              className="rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90"
            >
              Save Model Set
            </Link>
          </div>
        </div>
      </div>
    </AppShell>
  );
}

function Section({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-2xl border border-border bg-card p-5 space-y-4">
      <div>
        <h2 className="font-medium">{title}</h2>
        {subtitle && <p className="text-sm text-muted-foreground">{subtitle}</p>}
      </div>
      {children}
    </section>
  );
}
