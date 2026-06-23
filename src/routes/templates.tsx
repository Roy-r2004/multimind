import { createFileRoute, Link } from "@tanstack/react-router";
import { Plus, Pencil, Trash2, FileText } from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { TEMPLATES, type Template } from "@/lib/mock";
import { useState } from "react";
import { Modal } from "@/components/Modal";

export const Route = createFileRoute("/templates")({
  head: () => ({ meta: [{ title: "Templates — MultiAI" }] }),
  component: TemplatesPage,
});

function TemplatesPage() {
  const [templates, setTemplates] = useState<Template[]>(() => TEMPLATES.slice());
  const [showNew, setShowNew] = useState(false);
  const [name, setName] = useState("");
  const [category, setCategory] = useState("Learning");
  const [description, setDescription] = useState("");
  const [instructions, setInstructions] = useState("");

  function resetForm() {
    setName("");
    setCategory("Learning");
    setDescription("");
    setInstructions("");
  }

  function createTemplate() {
    const t: Template = {
      id: `t-${Date.now()}`,
      title: name.trim() || "Untitled template",
      description: description.trim() || "",
      category: category || "Custom",
      instructions: instructions.trim() || "",
    };
    setTemplates((prev) => [t, ...prev]);
    setShowNew(false);
    resetForm();
  }

  function deleteTemplate(id: string) {
    setTemplates((prev) => prev.filter((p) => p.id !== id));
  }

  return (
    <AppShell>
      <div className="mx-auto max-w-5xl px-6 py-10">
        <div className="flex items-end justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold">Templates</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Reusable instructions that shape every answer.
            </p>
          </div>
          <button
            onClick={() => setShowNew(true)}
            className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground"
          >
            <Plus className="size-4" /> New template
          </button>
        </div>

        {templates.length === 0 ? (
          <div className="mt-12 grid place-items-center rounded-2xl border border-dashed border-border bg-card p-12 text-center">
            <FileText className="size-6 text-muted-foreground" />
            <h3 className="mt-3 font-semibold">No templates yet</h3>
            <p className="mt-1 text-sm text-muted-foreground">
              Save your favorite prompt instructions for reuse.
            </p>
          </div>
        ) : (
          <div className="mt-8 grid gap-4 md:grid-cols-2">
            {templates.map((t) => (
              <div key={t.id} className="rounded-2xl border border-border bg-card p-5">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="font-medium">{t.title}</div>
                    <div className="mt-1 text-sm text-muted-foreground">{t.description}</div>
                  </div>
                  <span className="rounded-full bg-accent px-2 py-0.5 text-xs">{t.category}</span>
                </div>
                <div className="mt-3 rounded-lg border border-border bg-background p-3 text-xs text-muted-foreground line-clamp-3">
                  {t.instructions}
                </div>
                <div className="mt-3 flex items-center gap-1">
                  <Link to="/templates/new" className="rounded-md p-2 hover:bg-accent" title="Edit">
                    <Pencil className="size-4" />
                  </Link>
                  <button
                    onClick={() => deleteTemplate(t.id)}
                    className="rounded-md p-2 text-destructive hover:bg-destructive/10"
                    title="Delete"
                  >
                    <Trash2 className="size-4" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        <Modal open={showNew} onClose={() => { setShowNew(false); resetForm(); }} title="Create New Template" size="lg">
          <div className="space-y-4">
            <label className="block text-sm">
              <div className="mb-1 font-medium">Template Name</div>
              <input
                placeholder="e.g. Beginner Explanation"
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
              />
            </label>

            <label className="block text-sm">
              <div className="mb-1 font-medium">Category</div>
              <select
                value={category}
                onChange={(e) => setCategory(e.target.value)}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
              >
                {[
                  "Learning",
                  "Business",
                  "Decision",
                  "Coding",
                  "Research",
                  "Custom",
                ].map((c) => (
                  <option key={c}>{c}</option>
                ))}
              </select>
            </label>

            <label className="block text-sm">
              <div className="mb-1 font-medium">Short Description</div>
              <input
                placeholder="e.g. Beginner-friendly explanations."
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
              />
            </label>

            <label className="block text-sm">
              <div className="mb-1 font-medium">Template Instructions</div>
              <textarea
                placeholder="e.g. Explain like I am new to the topic. Use simple words, short examples, and avoid unnecessary complexity."
                value={instructions}
                onChange={(e) => setInstructions(e.target.value)}
                rows={6}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
              />
            </label>

            <div className="flex justify-end gap-2">
              <button
                onClick={() => { setShowNew(false); resetForm(); }}
                className="rounded-xl border border-border px-4 py-2 text-sm hover:bg-accent"
              >
                Cancel
              </button>
              <button
                onClick={createTemplate}
                className="rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
              >
                Create Template
              </button>
            </div>
          </div>
        </Modal>
      </div>
    </AppShell>
  );
}
