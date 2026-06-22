import { createFileRoute, Link } from "@tanstack/react-router";
import { Plus, Pencil, Trash2, FileText } from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { TEMPLATES } from "@/lib/mock";

export const Route = createFileRoute("/templates")({
  head: () => ({ meta: [{ title: "Templates — MultiAI" }] }),
  component: TemplatesPage,
});

function TemplatesPage() {
  return (
    <AppShell>
      <div className="mx-auto max-w-5xl px-6 py-10">
        <div className="flex items-end justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold">Templates</h1>
            <p className="mt-1 text-sm text-muted-foreground">Reusable instructions that shape every answer.</p>
          </div>
          <Link to="/templates/new" className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground"><Plus className="size-4" /> New template</Link>
        </div>

        {TEMPLATES.length === 0 ? (
          <div className="mt-12 grid place-items-center rounded-2xl border border-dashed border-border bg-card p-12 text-center">
            <FileText className="size-6 text-muted-foreground" />
            <h3 className="mt-3 font-semibold">No templates yet</h3>
            <p className="mt-1 text-sm text-muted-foreground">Save your favorite prompt instructions for reuse.</p>
          </div>
        ) : (
          <div className="mt-8 grid gap-4 md:grid-cols-2">
            {TEMPLATES.map((t) => (
              <div key={t.id} className="rounded-2xl border border-border bg-card p-5">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="font-medium">{t.title}</div>
                    <div className="mt-1 text-sm text-muted-foreground">{t.description}</div>
                  </div>
                  <span className="rounded-full bg-accent px-2 py-0.5 text-xs">{t.category}</span>
                </div>
                <div className="mt-3 rounded-lg border border-border bg-background p-3 text-xs text-muted-foreground line-clamp-3">{t.instructions}</div>
                <div className="mt-3 flex items-center gap-1">
                  <Link to="/templates/new" className="rounded-md p-2 hover:bg-accent" title="Edit"><Pencil className="size-4" /></Link>
                  <button className="rounded-md p-2 text-destructive hover:bg-destructive/10" title="Delete"><Trash2 className="size-4" /></button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </AppShell>
  );
}
