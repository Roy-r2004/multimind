import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowLeft,
  Loader2,
  Paperclip,
  Save,
  Star,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";
import { AppShell } from "@/components/AppShell";
import { GlassCard } from "@/components/cinematic/PageChrome";
import { LibraryFolderSelect } from "@/components/library/LibraryFolderSelect";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";
import type { ApiLibraryFolder, ApiLibraryItem, ApiLibraryLabel } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";
import { attachLibraryItemViaApi } from "@/lib/libraryAttach";
import { useChatStore } from "@/lib/store";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/library/$itemId")({
  head: () => ({ meta: [{ title: "Library Document — MultiAI" }] }),
  component: LibraryDocumentPage,
});

function LibraryDocumentPage() {
  const { itemId } = Route.useParams();
  const { authHeaders } = useAuth();
  const navigate = useNavigate();
  const { activeChatId, createChat, setActiveChatId } = useChatStore();
  const retainRef = useRef<string | null>(null);

  const [item, setItem] = useState<ApiLibraryItem | null>(null);
  const [folders, setFolders] = useState<ApiLibraryFolder[]>([]);
  const [labels, setLabels] = useState<ApiLibraryLabel[]>([]);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [folderId, setFolderId] = useState<string | null>(null);
  const [selectedLabelIds, setSelectedLabelIds] = useState<string[]>([]);
  const [newLabel, setNewLabel] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [attaching, setAttaching] = useState(false);

  const load = useCallback(async () => {
    const auth = authHeaders();
    if (!auth) {
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const [doc, folderList, labelList] = await Promise.all([
        api.library.getItem(auth, itemId),
        api.library.listFolders(auth),
        api.library.listLabels(auth),
      ]);
      if (doc.item_type !== "document") {
        toast.error("Only MultiMind Documents can be edited here");
        await navigate({ to: "/library" });
        return;
      }
      setItem(doc);
      setTitle(doc.title);
      setContent(doc.content_text ?? "");
      setFolderId(doc.folder_id ?? null);
      setSelectedLabelIds(doc.labels.map((label) => label.id));
      setFolders(folderList);
      setLabels(labelList);
      setDirty(false);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed to load document");
      await navigate({ to: "/library" });
    } finally {
      setLoading(false);
    }
  }, [authHeaders, itemId, navigate]);

  useEffect(() => {
    void load();
  }, [load]);

  async function save() {
    const auth = authHeaders();
    if (!auth || !item) return;
    setSaving(true);
    try {
      let labelIds = selectedLabelIds;
      if (newLabel.trim()) {
        const created = await api.library.createLabel(auth, newLabel.trim());
        labelIds = [...new Set([...labelIds, created.id])];
        setNewLabel("");
        setLabels((prev) => [...prev, created]);
      }
      const updated = await api.library.updateItem(auth, item.id, {
        title: title.trim() || item.title,
        content_text: content,
        folder_id: folderId || undefined,
        clear_folder: !folderId,
        label_ids: labelIds,
      });
      setItem(updated);
      setTitle(updated.title);
      setContent(updated.content_text ?? "");
      setFolderId(updated.folder_id ?? null);
      setSelectedLabelIds(updated.labels.map((label) => label.id));
      setDirty(false);
      toast.success("Saved");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  async function toggleFavorite() {
    const auth = authHeaders();
    if (!auth || !item) return;
    try {
      const updated = await api.library.updateItem(auth, item.id, {
        is_favorite: !item.is_favorite,
      });
      setItem(updated);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Update failed");
    }
  }

  async function remove() {
    const auth = authHeaders();
    if (!auth || !item) return;
    if (!window.confirm(`Delete “${item.title}”?`)) return;
    try {
      await api.library.deleteItem(auth, item.id);
      toast.success("Deleted");
      await navigate({ to: "/library" });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Delete failed");
    }
  }

  async function attachToChat() {
    const auth = authHeaders();
    if (!auth || !item) return;
    setAttaching(true);
    try {
      if (dirty) {
        await save();
      }
      const result = await attachLibraryItemViaApi({
        libraryItemId: item.id,
        activeChatId,
        createChat,
        activateChat: setActiveChatId,
        retainRef,
        attachFromLibrary: (chatId, libraryItemId) =>
          api.chats.attachFromLibrary(auth, chatId, libraryItemId),
      });
      if (!result.attachment) {
        toast.error("Could not attach to chat");
        return;
      }
      toast.success("Attached to current chat");
      await navigate({ to: "/chat" });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Attach failed");
    } finally {
      setAttaching(false);
    }
  }

  function toggleLabel(labelId: string) {
    setSelectedLabelIds((prev) =>
      prev.includes(labelId) ? prev.filter((id) => id !== labelId) : [...prev, labelId],
    );
    setDirty(true);
  }

  if (loading) {
    return (
      <AppShell>
        <div className="flex items-center justify-center gap-2 py-24 text-muted-foreground">
          <Loader2 className="size-4 animate-spin" />
          Loading document…
        </div>
      </AppShell>
    );
  }

  if (!item) return null;

  return (
    <AppShell>
      <div className="mx-auto flex w-full max-w-4xl flex-col gap-4 px-4 py-8 md:px-8">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <Link
            to="/library"
            className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="size-4" />
            Back to Library
          </Link>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => void toggleFavorite()}
              className="rounded-md border border-border p-2 hover:bg-muted"
              title={item.is_favorite ? "Unfavorite" : "Favorite"}
            >
              <Star
                className={cn(
                  "size-4",
                  item.is_favorite ? "fill-amber-400 text-amber-400" : "text-muted-foreground",
                )}
              />
            </button>
            <button
              type="button"
              onClick={() => void attachToChat()}
              disabled={attaching}
              className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-2 text-sm hover:bg-muted disabled:opacity-50"
            >
              {attaching ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Paperclip className="size-4" />
              )}
              Attach to Current Chat
            </button>
            <button
              type="button"
              onClick={() => void save()}
              disabled={saving || !dirty}
              className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
            >
              {saving ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}
              {dirty ? "Save" : "Saved"}
            </button>
            <button
              type="button"
              onClick={() => void remove()}
              className="rounded-md border border-border p-2 text-muted-foreground hover:bg-muted hover:text-destructive"
              title="Delete"
            >
              <Trash2 className="size-4" />
            </button>
          </div>
        </div>

        <GlassCard className="space-y-4 p-5">
          <input
            value={title}
            onChange={(e) => {
              setTitle(e.target.value);
              setDirty(true);
            }}
            className="w-full border-0 bg-transparent font-display text-2xl font-semibold outline-none"
            placeholder="Document title"
          />

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="library-doc-folder">Folder</Label>
              <LibraryFolderSelect
                id="library-doc-folder"
                folders={folders}
                value={folderId}
                onChange={(next) => {
                  setFolderId(next);
                  setDirty(true);
                }}
              />
            </div>
            <div className="space-y-1 text-sm">
              <span className="text-xs font-medium text-muted-foreground">Labels</span>
              <div className="flex flex-wrap gap-1.5">
                {labels.map((label) => (
                  <button
                    key={label.id}
                    type="button"
                    onClick={() => toggleLabel(label.id)}
                    className={cn(
                      "rounded-full border px-2.5 py-0.5 text-xs",
                      selectedLabelIds.includes(label.id)
                        ? "border-primary bg-primary/10 text-primary"
                        : "border-border text-muted-foreground",
                    )}
                  >
                    {label.name}
                  </button>
                ))}
              </div>
              <input
                value={newLabel}
                onChange={(e) => {
                  setNewLabel(e.target.value);
                  setDirty(true);
                }}
                placeholder="Add label…"
                className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-1.5 text-sm"
              />
            </div>
          </div>

          <textarea
            value={content}
            onChange={(e) => {
              setContent(e.target.value);
              setDirty(true);
            }}
            rows={22}
            placeholder="Write your document…"
            className="w-full resize-y rounded-lg border border-border bg-background/60 px-3 py-3 font-mono text-sm leading-relaxed outline-none focus:ring-2 focus:ring-ring"
          />
          <p className="text-[11px] text-muted-foreground">
            {dirty ? "Unsaved changes" : "All changes saved"} · MultiMind Document
          </p>
        </GlassCard>
      </div>
    </AppShell>
  );
}
