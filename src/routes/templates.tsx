import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { Plus, FileText, Loader2 } from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { GlassCard, PageHeader } from "@/components/cinematic/PageChrome";
import { Modal } from "@/components/Modal";
import { api } from "@/lib/api";
import type { ApiTemplate } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/templates")({
  head: () => ({ meta: [{ title: "Templates — MultiAI" }] }),
  component: TemplatesPage,
});

function TemplatesPage() {
  const { authHeaders, isAuthenticated } = useAuth();
  const [templates, setTemplates] = useState<ApiTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showModal, setShowModal] = useState(false);
  const [editing, setEditing] = useState<ApiTemplate | null>(null);
  const [name, setName] = useState("");
  const [category, setCategory] = useState("Learning");
  const [description, setDescription] = useState("");
  const [instructions, setInstructions] = useState("");
  const [saving, setSaving] = useState(false);

  const load = () => {
    const auth = authHeaders();
    if (!auth) {
      setLoading(false);
      return;
    }
    setLoading(true);
    void api.templates
      .list(auth)
      .then(setTemplates)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load templates"))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
  }, [authHeaders]);

  function resetForm() {
    setName("");
    setCategory("Learning");
    setDescription("");
    setInstructions("");
    setEditing(null);
  }

  function openCreate() {
    resetForm();
    setShowModal(true);
  }

  function openEdit(template: ApiTemplate) {
    setEditing(template);
    setName(template.title);
    setCategory(template.category || "Custom");
    setDescription(template.description);
    setInstructions(template.instructions);
    setShowModal(true);
  }

  function closeModal() {
    setShowModal(false);
    resetForm();
  }

  async function saveTemplate() {
    const auth = authHeaders();
    if (!auth) return;
    if (editing?.is_system) {
      closeModal();
      return;
    }
    setSaving(true);
    setError(null);
    try {
      if (editing) {
        setTemplates((prev) =>
          prev.map((t) =>
            t.id === editing.id
              ? {
                  ...t,
                  title: name.trim() || t.title,
                  description: description.trim(),
                  category: category || "Custom",
                  instructions: instructions.trim(),
                }
              : t,
          ),
        );
      } else {
        const created = await api.templates.create(auth, {
          title: name.trim() || "Untitled template",
          description: description.trim(),
          category: category || "Custom",
          instructions: instructions.trim(),
        });
        setTemplates((prev) => [created, ...prev]);
      }
      closeModal();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save template");
    } finally {
      setSaving(false);
    }
  }

  function categoryBadgeClass(value: string) {
    switch (value) {
      case "Learning":
        return "border-blue-200 bg-blue-50 text-blue-700";
      case "Business":
        return "border-emerald-200 bg-emerald-50 text-emerald-700";
      case "Decision":
        return "border-orange-200 bg-orange-50 text-orange-700";
      case "Research":
        return "border-violet-200 bg-violet-50 text-violet-700";
      default:
        return "border-border bg-accent text-foreground";
    }
  }

  return (
    <AppShell>
      <div className="mx-auto max-w-5xl px-6 py-10">
        <PageHeader
          eyebrow="Prompts"
          title="Templates"
          description="Reusable instructions that shape how your council answers."
          action={
            isAuthenticated ? (
              <button
                onClick={openCreate}
                className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground"
              >
                <Plus className="size-4" /> New template
              </button>
            ) : undefined
          }
        />

        {!isAuthenticated ? (
          <GlassCard className="mt-8 p-12 text-center text-sm text-muted-foreground">
            Log in to view organization templates.
          </GlassCard>
        ) : loading ? (
          <div className="flex justify-center py-20">
            <Loader2 className="size-6 animate-spin text-muted-foreground" />
          </div>
        ) : error && templates.length === 0 ? (
          <GlassCard className="mt-8 p-8 text-center text-sm text-destructive">{error}</GlassCard>
        ) : templates.length === 0 ? (
          <GlassCard className="mt-8 grid place-items-center p-12 text-center">
            <FileText className="size-6 text-muted-foreground" />
            <h3 className="mt-3 font-semibold">No templates yet</h3>
            <p className="mt-1 text-sm text-muted-foreground">Create one to reuse prompt instructions.</p>
          </GlassCard>
        ) : (
          <div className="mt-8 grid gap-4 md:grid-cols-2">
            {templates.map((t) => (
              <GlassCard key={t.id} className="p-5">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="font-medium">{t.title}</div>
                    <div className="mt-1 text-sm text-muted-foreground">{t.description}</div>
                  </div>
                  {t.category && (
                    <span
                      className={cn(
                        "rounded-full border px-2 py-0.5 text-xs font-medium",
                        categoryBadgeClass(t.category),
                      )}
                    >
                      {t.category}
                    </span>
                  )}
                </div>
                <div className="mt-3 rounded-lg border border-border bg-muted p-3 text-xs text-muted-foreground line-clamp-4">
                  {t.instructions}
                </div>
                {!t.is_system && (
                  <div className="mt-3">
                    <button
                      onClick={() => openEdit(t)}
                      className="rounded-md px-2.5 py-1.5 text-sm text-muted-foreground hover:bg-accent hover:text-foreground"
                    >
                      Edit
                    </button>
                  </div>
                )}
              </GlassCard>
            ))}
          </div>
        )}

        <Modal
          open={showModal}
          onClose={closeModal}
          title={editing ? (editing.is_system ? "View Template" : "Edit Template") : "Create New Template"}
          size="xl"
        >
          <div className="space-y-4">
            <label className="block text-sm">
              <div className="mb-1 font-medium">Template Name</div>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                disabled={!!editing?.is_system}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm disabled:opacity-60"
              />
            </label>
            <label className="block text-sm">
              <div className="mb-1 font-medium">Category</div>
              <select
                value={category}
                onChange={(e) => setCategory(e.target.value)}
                disabled={!!editing?.is_system}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm disabled:opacity-60"
              >
                {["Learning", "Business", "Decision", "Coding", "Research", "Custom"].map((c) => (
                  <option key={c}>{c}</option>
                ))}
              </select>
            </label>
            <label className="block text-sm">
              <div className="mb-1 font-medium">Short Description</div>
              <input
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                disabled={!!editing?.is_system}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm disabled:opacity-60"
              />
            </label>
            <label className="block text-sm">
              <div className="mb-1 font-medium">Template Instructions</div>
              <textarea
                value={instructions}
                onChange={(e) => setInstructions(e.target.value)}
                disabled={!!editing?.is_system}
                rows={8}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm disabled:opacity-60"
              />
            </label>
            <div className="flex justify-end gap-2">
              <button onClick={closeModal} className="rounded-xl border border-border px-4 py-2 text-sm hover:bg-accent">
                {editing?.is_system ? "Close" : "Cancel"}
              </button>
              {!editing?.is_system && (
                <button
                  onClick={() => void saveTemplate()}
                  disabled={saving}
                  className="rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
                >
                  {saving ? "Saving…" : editing ? "Save Changes" : "Create Template"}
                </button>
              )}
            </div>
          </div>
        </Modal>
      </div>
    </AppShell>
  );
}
