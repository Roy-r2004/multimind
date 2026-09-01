import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, Download, Loader2, Paperclip, Save, Star, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { AppShell } from "@/components/AppShell";
import { GlassCard } from "@/components/cinematic/PageChrome";
import { LibraryDocumentEditor } from "@/components/library/LibraryDocumentEditor";
import { LibraryFolderSelect } from "@/components/library/LibraryFolderSelect";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";
import type { ApiLibraryFolder, ApiLibraryItem, ApiLibraryLabel } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";
import { attachLibraryItemViaApi, saveLibraryDocumentBeforeAttach } from "@/lib/libraryAttach";
import { libraryFileContentView, libraryItemOpensDetail } from "@/lib/libraryContent";
import { formatLibraryBytes, formatLibraryUpdatedAt, libraryItemTypeLabel } from "@/lib/libraryUi";
import { useChatStore } from "@/lib/store";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/library/$itemId")({
  head: () => ({ meta: [{ title: "Library Item — MultiAI" }] }),
  component: LibraryItemPage,
});

function LibraryItemPage() {
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
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [attaching, setAttaching] = useState(false);
  const [downloading, setDownloading] = useState(false);

  const isDocument = item?.item_type === "document";
  const isFile = item?.item_type === "file";

  const load = useCallback(async () => {
    const auth = authHeaders();
    if (!auth) {
      setLoading(false);
      setItem(null);
      setLoadError("Sign in to open this Library item.");
      return;
    }
    setLoading(true);
    setLoadError(null);
    try {
      const [loaded, folderList, labelList] = await Promise.all([
        api.library.getItem(auth, itemId),
        api.library.listFolders(auth),
        api.library.listLabels(auth),
      ]);
      if (!libraryItemOpensDetail(loaded.item_type)) {
        const message = "This Library item cannot be opened here";
        toast.error(message);
        setItem(null);
        setLoadError(message);
        await navigate({ to: "/library" });
        return;
      }
      setItem(loaded);
      setTitle(loaded.title);
      setContent(loaded.content_text ?? "");
      setFolderId(loaded.folder_id ?? null);
      setSelectedLabelIds(loaded.labels.map((label) => label.id));
      setFolders(folderList);
      setLabels(labelList);
      setDirty(false);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Failed to load Library item";
      toast.error(message);
      setItem(null);
      setLoadError(message);
      await navigate({ to: "/library" });
    } finally {
      setLoading(false);
    }
  }, [authHeaders, itemId, navigate]);

  useEffect(() => {
    void load();
  }, [load]);

  async function save(): Promise<boolean> {
    const auth = authHeaders();
    if (!auth || !item) return false;
    setSaving(true);
    try {
      let labelIds = selectedLabelIds;
      if (newLabel.trim()) {
        const created = await api.library.createLabel(auth, newLabel.trim());
        labelIds = [...new Set([...labelIds, created.id])];
        setNewLabel("");
        setLabels((prev) => [...prev, created]);
      }
      const payload: Parameters<typeof api.library.updateItem>[2] = {
        title: title.trim() || item.title,
        folder_id: folderId || undefined,
        clear_folder: !folderId,
        label_ids: labelIds,
      };
      if (item.item_type === "document") {
        payload.content_text = content;
      }
      const updated = await api.library.updateItem(auth, item.id, payload);
      setItem(updated);
      setTitle(updated.title);
      setContent(updated.content_text ?? "");
      setFolderId(updated.folder_id ?? null);
      setSelectedLabelIds(updated.labels.map((label) => label.id));
      setDirty(false);
      toast.success("Saved");
      return true;
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Save failed");
      return false;
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

  async function downloadFile() {
    const auth = authHeaders();
    if (!auth || !item || item.item_type !== "file") return;
    setDownloading(true);
    try {
      const base = import.meta.env.VITE_API_URL ?? "/api/v1";
      const response = await fetch(`${base}/library/items/${item.id}/download`, {
        headers: auth,
      });
      if (!response.ok) {
        throw new Error("Download failed");
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = item.original_filename || item.title;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Download failed");
    } finally {
      setDownloading(false);
    }
  }

  async function attachToChat() {
    const auth = authHeaders();
    if (!auth || !item) return;
    setAttaching(true);
    try {
      if (!(await saveLibraryDocumentBeforeAttach(dirty, save))) return;
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
          Loading…
        </div>
      </AppShell>
    );
  }

  if (!item) {
    return (
      <AppShell>
        <div className="mx-auto flex w-full max-w-4xl flex-col items-start gap-4 px-4 py-8 md:px-8">
          <Link
            to="/library"
            className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="size-4" />
            Back to Library
          </Link>
          <p className="text-sm text-muted-foreground">
            {loadError ?? "This Library item could not be loaded."}
          </p>
        </div>
      </AppShell>
    );
  }

  const fileContent = isFile ? libraryFileContentView(item) : null;

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
            {isFile && (
              <button
                type="button"
                onClick={() => void downloadFile()}
                disabled={downloading}
                className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-2 text-sm hover:bg-muted disabled:opacity-50"
              >
                {downloading ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Download className="size-4" />
                )}
                Download
              </button>
            )}
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
          {isDocument ? (
            <input
              value={title}
              onChange={(e) => {
                setTitle(e.target.value);
                setDirty(true);
              }}
              className="w-full border-0 bg-transparent font-display text-2xl font-semibold outline-none"
              placeholder="Document title"
            />
          ) : (
            <div>
              <h1 className="font-display text-2xl font-semibold">{item.title}</h1>
              <p className="mt-1 text-sm text-muted-foreground">
                {libraryItemTypeLabel(item)}
                {item.size_bytes != null ? ` · ${formatLibraryBytes(item.size_bytes)}` : ""}
                {item.original_filename && item.original_filename !== item.title
                  ? ` · ${item.original_filename}`
                  : ""}
                {item.updated_at ? ` · Updated ${formatLibraryUpdatedAt(item.updated_at)}` : ""}
              </p>
            </div>
          )}

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="library-item-folder">Folder</Label>
              <LibraryFolderSelect
                id="library-item-folder"
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

          {isDocument ? (
            <>
              <LibraryDocumentEditor
                value={content}
                onChange={(next) => {
                  setContent(next);
                  setDirty(true);
                }}
                rows={22}
                placeholder="Write your document…"
                className="min-h-[32rem]"
              />
              <p className="text-[11px] text-muted-foreground">
                {dirty ? "Unsaved changes" : "All changes saved"} · MultiMind Document
              </p>
            </>
          ) : (
            <section className="space-y-2">
              <h2 className="text-sm font-medium text-foreground">Content</h2>
              {fileContent?.kind === "text" ? (
                <div className="max-h-[min(70vh,40rem)] overflow-auto rounded-lg border border-border bg-background/60 px-3 py-3">
                  <pre className="whitespace-pre-wrap break-words font-mono text-sm leading-relaxed text-foreground">
                    {fileContent.text}
                  </pre>
                </div>
              ) : (
                <div className="rounded-lg border border-dashed border-border bg-muted/30 px-4 py-8 text-center text-sm text-muted-foreground">
                  {fileContent?.message ??
                    "Preview is not available for this file. Download it to view it."}
                </div>
              )}
              <p className="text-[11px] text-muted-foreground">
                {dirty ? "Unsaved changes" : "Read-only file preview"} · Uploaded file
              </p>
            </section>
          )}
        </GlassCard>
      </div>
    </AppShell>
  );
}
