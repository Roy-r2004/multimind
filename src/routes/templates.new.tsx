import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import { AppShell } from "@/components/AppShell";

export const Route = createFileRoute("/templates/new")({
  head: () => ({ meta: [{ title: "New template — MultiAI" }] }),
  component: NewTemplate,
});

function NewTemplate() {
  const [name, setName] = useState("Explain simply");
  const [instructions, setInstructions] = useState("Explain like I'm new to the topic. Use simple words and short examples.");
  const [cat, setCat] = useState("Learning");

  return (
    <AppShell>
      <div className="mx-auto max-w-3xl px-6 py-10">
        <Link to="/templates" className="text-sm text-muted-foreground hover:text-foreground">← Back to Templates</Link>
        <h1 className="mt-2 text-2xl font-semibold">New template</h1>

        <div className="mt-8 grid gap-6 md:grid-cols-2">
          <div className="space-y-4">
            <label className="block text-sm">
              <div className="mb-1 font-medium">Name</div>
              <input value={name} onChange={(e) => setName(e.target.value)} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm" />
            </label>
            <label className="block text-sm">
              <div className="mb-1 font-medium">Category</div>
              <select value={cat} onChange={(e) => setCat(e.target.value)} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm">
                {["Learning", "Business", "Decision", "Coding", "Writing"].map((c) => <option key={c}>{c}</option>)}
              </select>
            </label>
            <label className="block text-sm">
              <div className="mb-1 font-medium">Instructions</div>
              <textarea value={instructions} onChange={(e) => setInstructions(e.target.value)} rows={8} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm" />
            </label>
            <div className="flex justify-end gap-2">
              <Link to="/templates" className="rounded-xl border border-border px-4 py-2 text-sm hover:bg-accent">Cancel</Link>
              <Link to="/templates" className="rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground">Save template</Link>
            </div>
          </div>
          <div>
            <div className="mb-2 text-sm font-medium">Preview</div>
            <div className="rounded-2xl border border-border bg-card p-4 text-sm">
              <div className="text-xs uppercase tracking-wider text-muted-foreground">Sample prompt</div>
              <div className="mt-2">“How does compound interest work?”</div>
              <div className="mt-4 text-xs uppercase tracking-wider text-muted-foreground">Becomes</div>
              <div className="mt-2 rounded-lg bg-accent/40 p-3 text-foreground/90">
                <strong>[{name}]</strong> {instructions} — Now answer: “How does compound interest work?”
              </div>
            </div>
          </div>
        </div>
      </div>
    </AppShell>
  );
}
