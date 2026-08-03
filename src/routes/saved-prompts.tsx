import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useCallback, useEffect, useState } from "react";
import { Loader2, MessageSquareText, Pencil, Search, Trash2, X } from "lucide-react";
import { toast } from "sonner";
import { AppShell } from "@/components/AppShell";
import { Modal } from "@/components/Modal";
import { GlassCard } from "@/components/cinematic/PageChrome";
import { MessageContent } from "@/components/chat/MessageContent";
import { api } from "@/lib/api";
import type { ApiContentLabel, ApiSavedPrompt } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";
import { useChatStore } from "@/lib/store";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/saved-prompts")({
  head: () => ({ meta: [{ title: "Saved Prompts — MultiAI" }] }),
  component: SavedPromptsPage,
});

function formatDate(value: string) {
  try {
    return new Date(value).toLocaleString(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
    });
  } catch {
    return value;
  }
}

function SavedPromptsPage() {
  const { authHeaders } = useAuth();
  const { setActiveChatId } = useChatStore();
  const navigate = useNavigate();
  const [labels, setLabels] = useState<ApiContentLabel[]>([]);
  const [prompts, setPrompts] = useState<ApiSavedPrompt[]>([]);
  const [selectedLabelId, setSelectedLabelId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [editing, setEditing] = useState<ApiSavedPrompt | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [editText, setEditText] = useState("");
  const [editLabelIds, setEditLabelIds] = useState<Set<string>>(() => new Set());
  const [savingEdit, setSavingEdit] = useState(false);

  const reload = useCallback(async () => {
    const auth = authHeaders();
    if (!auth) {
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const [labelList, promptList] = await Promise.all([
        api.contentLabels.list(auth),
        api.savedPrompts.list(auth, {
          q: query.trim() || undefined,
          label_id: selectedLabelId || undefined,
        }),
      ]);
      setLabels(labelList);
      setPrompts(promptList);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed to load prompts");
    } finally {
      setLoading(false);
    }
  }, [authHeaders, query, selectedLabelId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  async function renameLabel(labelId: string) {
    const auth = authHeaders();
    if (!auth || !renameValue.trim()) return;
    try {
      await api.contentLabels.rename(auth, labelId, renameValue.trim());
      setRenamingId(null);
      await reload();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Rename failed");
    }
  }

  async function deleteLabel(labelId: string) {
    const auth = authHeaders();
    if (!auth) return;
    if (!window.confirm("Delete this label? Prompts stay; only the label is removed.")) return;
    try {
      await api.contentLabels.delete(auth, labelId);
      if (selectedLabelId === labelId) setSelectedLabelId(null);
      await reload();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Delete failed");
    }
  }

  async function togglePromptLabel(prompt: ApiSavedPrompt, labelId: string) {
    const auth = authHeaders();
    if (!auth) return;
    const nextIds = prompt.labels.some((label) => label.id === labelId)
      ? prompt.labels.filter((label) => label.id !== labelId).map((label) => label.id)
      : [...prompt.labels.map((label) => label.id), labelId];
    try {
      await api.savedPrompts.update(auth, prompt.id, { label_ids: nextIds });
      await reload();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not update labels");
    }
  }

  async function deletePrompt(promptId: string) {
    const auth = authHeaders();
    if (!auth) return;
    if (!window.confirm("Delete this saved prompt?")) return;
    try {
      await api.savedPrompts.delete(auth, promptId);
      await reload();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Delete failed");
    }
  }

  function openEdit(prompt: ApiSavedPrompt) {
    setEditing(prompt);
    setEditTitle(prompt.title ?? "");
    setEditText(prompt.prompt_text);
    setEditLabelIds(new Set(prompt.labels.map((label) => label.id)));
  }

  async function saveEdit() {
    const auth = authHeaders();
    if (!auth || !editing) return;
    const text = editText.trim();
    if (!text) {
      toast.error("Prompt text is required");
      return;
    }
    setSavingEdit(true);
    try {
      await api.savedPrompts.update(auth, editing.id, {
        title: editTitle.trim(),
        prompt_text: text,
        label_ids: [...editLabelIds],
      });
      setEditing(null);
      await reload();
      toast.success("Prompt updated");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not update prompt");
    } finally {
      setSavingEdit(false);
    }
  }

  return (
    <AppShell>
      <div className="mx-auto max-w-6xl px-6 py-10">
        <div className="elevate-hero flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.32em] text-primary">
              Library
            </p>
            <h1 className="mt-1.5 font-display text-3xl font-semibold tracking-tight sm:text-[2.75rem] sm:leading-[1.1]">
              Saved Prompts
            </h1>
            <p className="mt-2.5 max-w-xl text-sm text-muted-foreground sm:text-[15px]">
              Saved questions with their final Verdict from the same turn — not individual model
              answers or full document snapshots.
            </p>
          </div>
          <div className="relative w-full max-w-xs">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search title or prompt text…"
              className="w-full rounded-xl border border-border bg-background py-2 pl-9 pr-3 text-sm outline-none focus:border-primary"
            />
          </div>
        </div>

        <div className="mt-8 grid gap-6 lg:grid-cols-[220px_1fr]">
          <aside className="space-y-2">
            <button
              type="button"
              onClick={() => setSelectedLabelId(null)}
              className={cn(
                "flex w-full items-center justify-between rounded-lg px-3 py-2 text-sm",
                selectedLabelId === null
                  ? "bg-primary/10 font-medium text-primary"
                  : "text-muted-foreground hover:bg-accent",
              )}
            >
              All prompts
              <span className="text-xs">{prompts.length}</span>
            </button>
            {labels.map((label) => (
              <div key={label.id} className="rounded-lg border border-transparent hover:border-border">
                {renamingId === label.id ? (
                  <div className="flex gap-1 p-1">
                    <input
                      value={renameValue}
                      onChange={(event) => setRenameValue(event.target.value)}
                      className="min-w-0 flex-1 rounded border border-border px-2 py-1 text-xs"
                    />
                    <button
                      type="button"
                      onClick={() => void renameLabel(label.id)}
                      className="rounded bg-primary px-2 text-xs text-primary-foreground"
                    >
                      Save
                    </button>
                  </div>
                ) : (
                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      onClick={() => setSelectedLabelId(label.id)}
                      className={cn(
                        "min-w-0 flex-1 truncated rounded-lg px-3 py-2 text-left text-sm",
                        selectedLabelId === label.id
                          ? "bg-primary/10 font-medium text-primary"
                          : "text-muted-foreground hover:bg-accent",
                      )}
                    >
                      <span className="block truncate">{label.name}</span>
                    </button>
                    <button
                      type="button"
                      aria-label="Rename label"
                      onClick={() => {
                        setRenamingId(label.id);
                        setRenameValue(label.name);
                      }}
                      className="rounded p-1.5 text-muted-foreground hover:bg-accent"
                    >
                      <Pencil className="size-3.5" />
                    </button>
                    <button
                      type="button"
                      aria-label="Delete label"
                      onClick={() => void deleteLabel(label.id)}
                      className="rounded p-1.5 text-destructive hover:bg-destructive/10"
                    >
                      <Trash2 className="size-3.5" />
                    </button>
                  </div>
                )}
              </div>
            ))}
          </aside>

          <div className="space-y-3">
            {loading ? (
              <div className="flex justify-center py-16">
                <Loader2 className="size-5 animate-spin text-muted-foreground" />
              </div>
            ) : prompts.length === 0 ? (
              <GlassCard className="p-10 text-center text-sm text-muted-foreground">
                No saved prompts yet
              </GlassCard>
            ) : (
              prompts.map((prompt) => (
                <GlassCard key={prompt.id} className="p-5">
                  <div className="flex flex-wrap items-start gap-3">
                    <span className="grid size-9 place-items-center rounded-lg bg-primary/10 text-primary">
                      <MessageSquareText className="size-4" />
                    </span>
                    <div className="min-w-0 flex-1 space-y-3">
                      {prompt.title ? (
                        <h2 className="font-medium">{prompt.title}</h2>
                      ) : null}
                      <div className="space-y-1.5">
                        <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                          Prompt
                        </p>
                        <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground">
                          {prompt.prompt_text}
                        </p>
                      </div>
                      {prompt.verdict_text?.trim() ? (
                        <div className="space-y-1.5 rounded-lg border border-primary/15 bg-primary/[0.03] px-3 py-2.5">
                          <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-primary">
                            Verdict
                          </p>
                          <MessageContent muted>{prompt.verdict_text}</MessageContent>
                        </div>
                      ) : null}
                      <p className="text-xs text-muted-foreground">
                        {formatDate(prompt.created_at)}
                      </p>
                      <div className="flex flex-wrap gap-1.5">
                        {prompt.labels.map((label) => (
                          <span
                            key={label.id}
                            className="rounded-full border border-border px-2 py-0.5 text-[11px]"
                          >
                            {label.name}
                          </span>
                        ))}
                      </div>
                      {labels.length > 0 && (
                        <div className="flex flex-wrap gap-1.5">
                          <span className="text-[11px] text-muted-foreground">Move/toggle:</span>
                          {labels.map((label) => (
                            <button
                              key={label.id}
                              type="button"
                              onClick={() => void togglePromptLabel(prompt, label.id)}
                              className="rounded-full border border-dashed border-border px-2 py-0.5 text-[11px] hover:border-primary hover:text-primary"
                            >
                              {label.name}
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {prompt.chat_id && (
                        <button
                          type="button"
                          onClick={() => {
                            const chatId = prompt.chat_id!;
                            setActiveChatId(chatId);
                            const turnQuery = prompt.turn_id
                              ? `&turnId=${encodeURIComponent(prompt.turn_id)}`
                              : "";
                            void navigate({
                              href: `/chat?chatId=${encodeURIComponent(chatId)}${turnQuery}`,
                            });
                          }}
                          className="rounded-lg border border-border px-2.5 py-1.5 text-xs hover:bg-accent"
                        >
                          Open chat
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() => openEdit(prompt)}
                        className="rounded-lg border border-border px-2.5 py-1.5 text-xs hover:bg-accent"
                      >
                        Edit
                      </button>
                      <button
                        type="button"
                        onClick={() => void deletePrompt(prompt.id)}
                        className="rounded-lg border border-border px-2.5 py-1.5 text-xs text-destructive hover:bg-destructive/10"
                      >
                        Delete
                      </button>
                    </div>
                  </div>
                </GlassCard>
              ))
            )}
          </div>
        </div>

        <p className="mt-8 text-center text-xs text-muted-foreground">
          Looking for full turn snapshots?{" "}
          <Link to="/saved-documents" className="text-primary hover:underline">
            Saved Documents
          </Link>
        </p>
      </div>

      <Modal
        open={Boolean(editing)}
        onClose={() => {
          if (!savingEdit) setEditing(null);
        }}
        title="Edit saved prompt"
        size="md"
      >
        <div className="space-y-4">
          <label className="block space-y-1.5">
            <span className="text-xs font-medium text-muted-foreground">Title (optional)</span>
            <input
              value={editTitle}
              onChange={(event) => setEditTitle(event.target.value)}
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary"
            />
          </label>
          <label className="block space-y-1.5">
            <span className="text-xs font-medium text-muted-foreground">Prompt text</span>
            <textarea
              value={editText}
              onChange={(event) => setEditText(event.target.value)}
              rows={5}
              className="w-full resize-y rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary"
            />
          </label>
          <div className="flex flex-wrap gap-1.5">
            {labels.map((label) => {
              const selected = editLabelIds.has(label.id);
              return (
                <button
                  key={label.id}
                  type="button"
                  onClick={() =>
                    setEditLabelIds((prev) => {
                      const next = new Set(prev);
                      if (next.has(label.id)) next.delete(label.id);
                      else next.add(label.id);
                      return next;
                    })
                  }
                  className={cn(
                    "rounded-full border px-2.5 py-1 text-xs font-medium",
                    selected
                      ? "border-primary/40 bg-primary/10 text-primary"
                      : "border-border text-muted-foreground hover:bg-accent",
                  )}
                >
                  {label.name}
                </button>
              );
            })}
          </div>
          <div className="flex justify-end gap-2">
            <button
              type="button"
              disabled={savingEdit}
              onClick={() => setEditing(null)}
              className="inline-flex items-center gap-1 rounded-lg border border-border px-3 py-2 text-sm hover:bg-accent"
            >
              <X className="size-3.5" /> Cancel
            </button>
            <button
              type="button"
              disabled={savingEdit || !editText.trim()}
              onClick={() => void saveEdit()}
              className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            >
              {savingEdit ? <Loader2 className="size-3.5 animate-spin" /> : null}
              Save changes
            </button>
          </div>
        </div>
      </Modal>
    </AppShell>
  );
}
