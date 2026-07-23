import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useCallback, useEffect, useState } from "react";
import { FileText, Loader2, Pencil, Search, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { AppShell } from "@/components/AppShell";
import { GlassCard } from "@/components/cinematic/PageChrome";
import { api } from "@/lib/api";
import type { ApiContentLabel, ApiSavedDocument } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";
import { useChatStore } from "@/lib/store";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/saved-documents")({
  head: () => ({ meta: [{ title: "Saved Documents — MultiAI" }] }),
  component: SavedDocumentsPage,
});

function SavedDocumentsPage() {
  const { authHeaders } = useAuth();
  const { setActiveChatId } = useChatStore();
  const navigate = useNavigate();
  const [labels, setLabels] = useState<ApiContentLabel[]>([]);
  const [docs, setDocs] = useState<ApiSavedDocument[]>([]);
  const [selectedLabelId, setSelectedLabelId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");

  const reload = useCallback(async () => {
    const auth = authHeaders();
    if (!auth) {
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const [labelList, docList] = await Promise.all([
        api.contentLabels.list(auth),
        api.savedDocuments.list(auth, {
          q: query.trim() || undefined,
          label_id: selectedLabelId || undefined,
        }),
      ]);
      setLabels(labelList);
      setDocs(docList);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed to load documents");
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
    if (!window.confirm("Delete this label? Documents stay; only the label is removed.")) return;
    try {
      await api.contentLabels.delete(auth, labelId);
      if (selectedLabelId === labelId) setSelectedLabelId(null);
      await reload();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Delete failed");
    }
  }

  async function moveDocument(doc: ApiSavedDocument, labelId: string) {
    const auth = authHeaders();
    if (!auth) return;
    const nextIds = doc.labels.some((label) => label.id === labelId)
      ? doc.labels.filter((label) => label.id !== labelId).map((label) => label.id)
      : [...doc.labels.map((label) => label.id), labelId];
    try {
      await api.savedDocuments.update(auth, doc.id, { label_ids: nextIds });
      await reload();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not update labels");
    }
  }

  async function deleteDocument(documentId: string) {
    const auth = authHeaders();
    if (!auth) return;
    if (!window.confirm("Delete this saved document?")) return;
    try {
      await api.savedDocuments.delete(auth, documentId);
      await reload();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Delete failed");
    }
  }

  return (
    <AppShell>
      <div className="mx-auto max-w-6xl px-6 py-10">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-primary">
              Library
            </p>
            <h1 className="mt-1 font-display text-3xl font-bold tracking-tight">Saved documents</h1>
            <p className="mt-2 max-w-xl text-sm text-muted-foreground">
              Full chat turns you saved under labels. Separate from bookmark Saved Verdicts and from
              Challenge Lessons.
            </p>
          </div>
          <div className="relative w-full max-w-xs">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search name, label, chat…"
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
              All documents
              <span className="text-xs">{docs.length}</span>
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
                      <span className="text-[11px] opacity-70">{label.document_count} docs</span>
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
            ) : docs.length === 0 ? (
              <GlassCard className="p-10 text-center text-sm text-muted-foreground">
                No saved documents yet. In chat, open a turn menu and choose{" "}
                <strong className="text-foreground">Save as document</strong>.
              </GlassCard>
            ) : (
              docs.map((doc) => (
                <GlassCard key={doc.id} className="p-5">
                  <div className="flex flex-wrap items-start gap-3">
                    <span className="grid size-9 place-items-center rounded-lg bg-primary/10 text-primary">
                      <FileText className="size-4" />
                    </span>
                    <div className="min-w-0 flex-1">
                      <h2 className="font-medium">{doc.name}</h2>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {doc.chat_title}
                        {doc.project_name ? ` · ${doc.project_name}` : ""}
                      </p>
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {doc.labels.map((label) => (
                          <span
                            key={label.id}
                            className="rounded-full border border-border px-2 py-0.5 text-[11px]"
                          >
                            {label.name}
                          </span>
                        ))}
                      </div>
                      {labels.length > 0 && (
                        <div className="mt-3 flex flex-wrap gap-1.5">
                          <span className="text-[11px] text-muted-foreground">Move/toggle:</span>
                          {labels.map((label) => (
                            <button
                              key={label.id}
                              type="button"
                              onClick={() => void moveDocument(doc, label.id)}
                              className="rounded-full border border-dashed border-border px-2 py-0.5 text-[11px] hover:border-primary hover:text-primary"
                            >
                              {label.name}
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                    <div className="flex gap-2">
                      {doc.chat_id && doc.turn_id && (
                        <button
                          type="button"
                          onClick={() => {
                            const chatId = doc.chat_id!;
                            const turnId = doc.turn_id!;
                            setActiveChatId(chatId);
                            void navigate({
                              href: `/chat?chatId=${encodeURIComponent(chatId)}&turnId=${encodeURIComponent(turnId)}`,
                            });
                          }}
                          className="rounded-lg border border-border px-2.5 py-1.5 text-xs hover:bg-accent"
                        >
                          Open turn
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() => void deleteDocument(doc.id)}
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
          Looking for bookmarks only?{" "}
          <Link to="/saved-verdicts" className="text-primary hover:underline">
            Saved Verdicts
          </Link>
        </p>
      </div>
    </AppShell>
  );
}
