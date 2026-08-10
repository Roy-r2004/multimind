import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  Download,
  File,
  FileSpreadsheet,
  FileText,
  Folder,
  FolderInput,
  FolderPlus,
  Loader2,
  Paperclip,
  Pencil,
  Plus,
  Search,
  Star,
  Trash2,
  Upload,
} from "lucide-react";
import { toast } from "sonner";
import { AppShell } from "@/components/AppShell";
import { GlassCard } from "@/components/cinematic/PageChrome";
import { LibraryFolderSelect } from "@/components/library/LibraryFolderSelect";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import type { ApiLibraryFolder, ApiLibraryItem, ApiLibraryLabel } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";
import { COMPOSER_FILE_ACCEPT } from "@/lib/composerAttachments";
import { attachLibraryItemViaApi } from "@/lib/libraryAttach";
import {
  buildLibraryFolderTree,
  formatLibraryBytes,
  formatLibraryUpdatedAt,
  libraryFolderPath,
  libraryItemTypeLabel,
  type LibraryFolderNode,
} from "@/lib/libraryUi";
import { useChatStore } from "@/lib/store";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/library")({
  head: () => ({ meta: [{ title: "Library — MultiAI" }] }),
  component: LibraryPage,
});

type ViewMode =
  | { kind: "all" }
  | { kind: "favorites" }
  | { kind: "recent" }
  | { kind: "folder"; folderId: string }
  | { kind: "unfiled" }
  | { kind: "label"; labelId: string };

function LibraryPage() {
  const { authHeaders } = useAuth();
  const navigate = useNavigate();
  const { activeChatId, createChat, setActiveChatId } = useChatStore();
  const retainRef = useRef<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [folders, setFolders] = useState<ApiLibraryFolder[]>([]);
  const [labels, setLabels] = useState<ApiLibraryLabel[]>([]);
  const [items, setItems] = useState<ApiLibraryItem[]>([]);
  const [view, setView] = useState<ViewMode>({ kind: "all" });
  const [query, setQuery] = useState("");
  const [fileTypeFilter, setFileTypeFilter] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [attachingId, setAttachingId] = useState<string | null>(null);

  const [createOpen, setCreateOpen] = useState(false);
  const [createTitle, setCreateTitle] = useState("");
  const [createBody, setCreateBody] = useState("");
  const [createFolderId, setCreateFolderId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploadFolderId, setUploadFolderId] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);

  const [moveItemTarget, setMoveItemTarget] = useState<ApiLibraryItem | null>(null);
  const [moveFolderId, setMoveFolderId] = useState<string | null>(null);
  const [moving, setMoving] = useState(false);

  const folderTree = useMemo(() => buildLibraryFolderTree(folders), [folders]);

  function defaultFolderForView(): string | null {
    return view.kind === "folder" ? view.folderId : null;
  }

  const reloadMeta = useCallback(async () => {
    const auth = authHeaders();
    if (!auth) return;
    const [folderList, labelList] = await Promise.all([
      api.library.listFolders(auth),
      api.library.listLabels(auth),
    ]);
    setFolders(folderList);
    setLabels(labelList);
  }, [authHeaders]);

  const reloadItems = useCallback(async () => {
    const auth = authHeaders();
    if (!auth) {
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const params: Parameters<typeof api.library.listItems>[1] = {
        q: query.trim() || undefined,
      };
      if (view.kind === "favorites") params.favorites = true;
      if (view.kind === "recent") params.recent = true;
      if (view.kind === "folder") params.folder_id = view.folderId;
      if (view.kind === "unfiled") params.unfiled = true;
      if (view.kind === "label") params.label_id = view.labelId;
      if (fileTypeFilter === "document") params.item_type = "document";
      if (fileTypeFilter === "file") params.item_type = "file";

      let list = await api.library.listItems(auth, params);
      if (fileTypeFilter && fileTypeFilter !== "document" && fileTypeFilter !== "file") {
        const ext = fileTypeFilter.toLowerCase();
        list = list.filter((item) =>
          (item.original_filename || item.title || "").toLowerCase().endsWith(ext),
        );
      }
      setItems(list);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed to load Library");
    } finally {
      setLoading(false);
    }
  }, [authHeaders, query, view, fileTypeFilter]);

  useEffect(() => {
    void reloadMeta();
  }, [reloadMeta]);

  useEffect(() => {
    void reloadItems();
  }, [reloadItems]);

  async function createFolder(parentId: string | null) {
    const auth = authHeaders();
    if (!auth) return;
    const name = window.prompt("Folder name");
    if (!name?.trim()) return;
    try {
      await api.library.createFolder(auth, {
        name: name.trim(),
        parent_id: parentId,
      });
      await reloadMeta();
      toast.success("Folder created");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not create folder");
    }
  }

  function openCreateDocument() {
    setCreateTitle("");
    setCreateBody("");
    setCreateFolderId(defaultFolderForView());
    setCreateOpen(true);
  }

  async function submitCreateDocument() {
    const auth = authHeaders();
    if (!auth) return;
    const title = createTitle.trim();
    if (!title) {
      toast.error("Title is required");
      return;
    }
    setCreating(true);
    try {
      const doc = await api.library.createDocument(auth, {
        title,
        content_text: createBody,
        folder_id: createFolderId,
      });
      setCreateOpen(false);
      await reloadItems();
      await reloadMeta();
      toast.success("Document created");
      await navigate({ to: "/library/$itemId", params: { itemId: doc.id } });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not create document");
    } finally {
      setCreating(false);
    }
  }

  function openUploadDialog() {
    setUploadFolderId(defaultFolderForView());
    setUploadOpen(true);
  }

  async function onUploadSelected(fileList: FileList | null) {
    const auth = authHeaders();
    if (!auth || !fileList?.length) return;
    setUploading(true);
    try {
      for (const file of Array.from(fileList)) {
        await api.library.uploadFile(auth, file, {
          folder_id: uploadFolderId ?? undefined,
        });
      }
      toast.success(fileList.length === 1 ? "File uploaded" : "Files uploaded");
      setUploadOpen(false);
      await reloadItems();
      await reloadMeta();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Upload failed");
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  async function toggleFavorite(item: ApiLibraryItem) {
    const auth = authHeaders();
    if (!auth) return;
    try {
      await api.library.updateItem(auth, item.id, { is_favorite: !item.is_favorite });
      await reloadItems();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Update failed");
    }
  }

  async function deleteItem(item: ApiLibraryItem) {
    const auth = authHeaders();
    if (!auth) return;
    if (!window.confirm(`Delete “${item.title}”?`)) return;
    try {
      await api.library.deleteItem(auth, item.id);
      toast.success("Deleted");
      await reloadItems();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Delete failed");
    }
  }

  async function renameItem(item: ApiLibraryItem) {
    const auth = authHeaders();
    if (!auth) return;
    const next = window.prompt("Display name", item.title);
    if (!next?.trim() || next.trim() === item.title) return;
    try {
      await api.library.updateItem(auth, item.id, { title: next.trim() });
      await reloadItems();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Rename failed");
    }
  }

  function openMoveItem(item: ApiLibraryItem) {
    setMoveItemTarget(item);
    setMoveFolderId(item.folder_id);
  }

  async function submitMoveItem() {
    const auth = authHeaders();
    if (!auth || !moveItemTarget) return;
    setMoving(true);
    try {
      await api.library.updateItem(auth, moveItemTarget.id, {
        folder_id: moveFolderId ?? undefined,
        clear_folder: !moveFolderId,
      });
      setMoveItemTarget(null);
      await reloadItems();
      toast.success("Moved");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Move failed");
    } finally {
      setMoving(false);
    }
  }

  async function downloadItem(item: ApiLibraryItem) {
    const auth = authHeaders();
    if (!auth || item.item_type !== "file") return;
    try {
      const base = import.meta.env.VITE_API_URL ?? "/api/v1";
      const response = await fetch(`${base}/library/items/${item.id}/download`, {
        headers: {
          Authorization: `Bearer ${auth.token}`,
          ...(auth.orgId ? { "X-Org-Id": auth.orgId } : {}),
        },
      });
      if (!response.ok) {
        throw new Error("Download failed");
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = item.original_filename || item.title;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Download failed");
    }
  }

  async function attachToChat(item: ApiLibraryItem) {
    const auth = authHeaders();
    if (!auth) return;
    setAttachingId(item.id);
    try {
      const result = await attachLibraryItemViaApi({
        libraryItemId: item.id,
        activeChatId,
        createChat,
        activateChat: setActiveChatId,
        retainRef,
        attachFromLibrary: (chatId, libraryItemId) =>
          api.chats.attachFromLibrary(auth, chatId, libraryItemId),
      });
      if (!result.attachment || !result.chatId) {
        toast.error("Could not attach to chat");
        return;
      }
      toast.success("Attached to current chat");
      await navigate({ to: "/chat" });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Attach failed");
    } finally {
      setAttachingId(null);
    }
  }

  const breadcrumb = view.kind === "folder" ? libraryFolderPath(folders, view.folderId) : [];

  return (
    <AppShell>
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-4 py-8 md:px-8">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="font-display text-3xl font-semibold tracking-tight">Library</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Files and MultiMind Documents you can organize and attach to any chat.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button type="button" onClick={openCreateDocument}>
              <Plus className="size-4" />
              Create Document
            </Button>
            <Button type="button" variant="outline" onClick={openUploadDialog}>
              <Upload className="size-4" />
              Upload File
            </Button>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept={COMPOSER_FILE_ACCEPT}
              className="hidden"
              onChange={(e) => void onUploadSelected(e.target.files)}
            />
          </div>
        </div>

        <div className="grid gap-6 lg:grid-cols-[240px_minmax(0,1fr)]">
          <GlassCard className="h-fit space-y-4 p-4">
            <nav className="space-y-1 text-sm">
              <SideLink active={view.kind === "all"} onClick={() => setView({ kind: "all" })}>
                All Items
              </SideLink>
              <SideLink
                active={view.kind === "favorites"}
                onClick={() => setView({ kind: "favorites" })}
              >
                Favorites
              </SideLink>
              <SideLink active={view.kind === "recent"} onClick={() => setView({ kind: "recent" })}>
                Recent
              </SideLink>
              <SideLink active={view.kind === "unfiled"} onClick={() => setView({ kind: "unfiled" })}>
                Unfiled
              </SideLink>
            </nav>

            <div>
              <div className="mb-2 flex items-center justify-between">
                <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  Folders
                </p>
                <button
                  type="button"
                  className="rounded p-1 text-muted-foreground hover:bg-muted"
                  title="New root folder"
                  onClick={() => void createFolder(null)}
                >
                  <FolderPlus className="size-3.5" />
                </button>
              </div>
              <div className="space-y-0.5">
                {folderTree.map((node) => (
                  <FolderTree
                    key={node.id}
                    node={node}
                    depth={0}
                    activeId={view.kind === "folder" ? view.folderId : null}
                    onSelect={(id) => setView({ kind: "folder", folderId: id })}
                    onAddChild={(id) => void createFolder(id)}
                  />
                ))}
                {folderTree.length === 0 && (
                  <p className="px-2 text-xs text-muted-foreground">No folders yet</p>
                )}
              </div>
            </div>

            <div>
              <div className="mb-2 flex items-center justify-between">
                <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  Labels
                </p>
                <button
                  type="button"
                  className="rounded p-1 text-muted-foreground hover:bg-muted"
                  title="New label"
                  onClick={() => {
                    void (async () => {
                      const auth = authHeaders();
                      if (!auth) return;
                      const name = window.prompt("Label name");
                      if (!name?.trim()) return;
                      try {
                        await api.library.createLabel(auth, name.trim());
                        await reloadMeta();
                      } catch (error) {
                        toast.error(
                          error instanceof Error ? error.message : "Could not create label",
                        );
                      }
                    })();
                  }}
                >
                  <Plus className="size-3.5" />
                </button>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {labels.map((label) => (
                  <button
                    key={label.id}
                    type="button"
                    onClick={() => setView({ kind: "label", labelId: label.id })}
                    className={cn(
                      "rounded-full border px-2.5 py-0.5 text-xs",
                      view.kind === "label" && view.labelId === label.id
                        ? "border-primary bg-primary/10 text-primary"
                        : "border-border text-muted-foreground hover:bg-muted",
                    )}
                  >
                    {label.name}
                  </button>
                ))}
                {labels.length === 0 && (
                  <p className="text-xs text-muted-foreground">No labels yet</p>
                )}
              </div>
            </div>
          </GlassCard>

          <div className="space-y-4">
            <div className="flex flex-wrap gap-3">
              <div className="relative min-w-[220px] flex-1">
                <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search title, filename, labels, content…"
                  className="w-full rounded-lg border border-border bg-background/70 py-2 pr-3 pl-9 text-sm outline-none focus:ring-2 focus:ring-ring"
                />
              </div>
              <select
                value={fileTypeFilter}
                onChange={(e) => setFileTypeFilter(e.target.value)}
                className="rounded-lg border border-border bg-background/70 px-3 py-2 text-sm"
              >
                <option value="">All types</option>
                <option value="document">MultiMind Document</option>
                <option value="file">Uploaded files</option>
                <option value=".pdf">PDF</option>
                <option value=".docx">Word</option>
                <option value=".xlsx">Excel</option>
                <option value=".txt">Text</option>
              </select>
            </div>

            {breadcrumb.length > 0 && (
              <div className="flex flex-wrap items-center gap-1 text-sm text-muted-foreground">
                <button type="button" className="hover:text-foreground" onClick={() => setView({ kind: "all" })}>
                  Library
                </button>
                {breadcrumb.map((folder) => (
                  <span key={folder.id} className="flex items-center gap-1">
                    <span>/</span>
                    <button
                      type="button"
                      className="hover:text-foreground"
                      onClick={() => setView({ kind: "folder", folderId: folder.id })}
                    >
                      {folder.name}
                    </button>
                  </span>
                ))}
              </div>
            )}

            <GlassCard className="overflow-hidden">
              {loading ? (
                <div className="flex items-center justify-center gap-2 py-16 text-muted-foreground">
                  <Loader2 className="size-4 animate-spin" />
                  Loading…
                </div>
              ) : items.length === 0 ? (
                <div className="px-6 py-16 text-center text-sm text-muted-foreground">
                  Nothing here yet. Create a document or upload a file.
                </div>
              ) : (
                <ul className="divide-y divide-border">
                  {items.map((item) => (
                    <li
                      key={item.id}
                      className="flex flex-wrap items-start justify-between gap-3 px-4 py-3 hover:bg-muted/40"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="flex items-start gap-3">
                          <ItemIcon item={item} />
                          <div className="min-w-0">
                            {item.item_type === "document" ? (
                              <Link
                                to="/library/$itemId"
                                params={{ itemId: item.id }}
                                className="truncate font-medium hover:underline"
                              >
                                {item.title}
                              </Link>
                            ) : (
                              <p className="truncate font-medium">{item.title}</p>
                            )}
                            <p className="mt-0.5 text-xs text-muted-foreground">
                              {libraryItemTypeLabel(item)}
                              {item.size_bytes != null ? ` · ${formatLibraryBytes(item.size_bytes)}` : ""}
                              {item.original_filename && item.original_filename !== item.title
                                ? ` · ${item.original_filename}`
                                : ""}
                            </p>
                            <div className="mt-1 flex flex-wrap gap-1">
                              {item.labels.map((label) => (
                                <span
                                  key={label.id}
                                  className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground"
                                >
                                  {label.name}
                                </span>
                              ))}
                            </div>
                            <p className="mt-1 text-[11px] text-muted-foreground">
                              Updated {formatLibraryUpdatedAt(item.updated_at)}
                            </p>
                          </div>
                        </div>
                      </div>
                      <div className="flex flex-wrap items-center gap-1">
                        <button
                          type="button"
                          title={item.is_favorite ? "Unfavorite" : "Favorite"}
                          onClick={() => void toggleFavorite(item)}
                          className="rounded-md p-2 hover:bg-muted"
                        >
                          <Star
                            className={cn(
                              "size-4",
                              item.is_favorite
                                ? "fill-amber-400 text-amber-400"
                                : "text-muted-foreground",
                            )}
                          />
                        </button>
                        <button
                          type="button"
                          title="Rename"
                          onClick={() => void renameItem(item)}
                          className="rounded-md p-2 text-muted-foreground hover:bg-muted"
                        >
                          <Pencil className="size-4" />
                        </button>
                        <button
                          type="button"
                          title="Move to folder"
                          onClick={() => openMoveItem(item)}
                          className="rounded-md p-2 text-muted-foreground hover:bg-muted"
                        >
                          <FolderInput className="size-4" />
                        </button>
                        {item.item_type === "file" && (
                          <button
                            type="button"
                            title="Download"
                            onClick={() => void downloadItem(item)}
                            className="rounded-md p-2 text-muted-foreground hover:bg-muted"
                          >
                            <Download className="size-4" />
                          </button>
                        )}
                        <button
                          type="button"
                          title="Attach to Current Chat"
                          disabled={attachingId === item.id}
                          onClick={() => void attachToChat(item)}
                          className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs hover:bg-muted disabled:opacity-50"
                        >
                          {attachingId === item.id ? (
                            <Loader2 className="size-3.5 animate-spin" />
                          ) : (
                            <Paperclip className="size-3.5" />
                          )}
                          Attach to Current Chat
                        </button>
                        <button
                          type="button"
                          title="Delete"
                          onClick={() => void deleteItem(item)}
                          className="rounded-md p-2 text-muted-foreground hover:bg-muted hover:text-destructive"
                        >
                          <Trash2 className="size-4" />
                        </button>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </GlassCard>
          </div>
        </div>
      </div>

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>Create Document</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="library-create-title">Title</Label>
              <Input
                id="library-create-title"
                value={createTitle}
                onChange={(e) => setCreateTitle(e.target.value)}
                placeholder="Document title"
                autoFocus
                disabled={creating}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="library-create-body">Body</Label>
              <Textarea
                id="library-create-body"
                value={createBody}
                onChange={(e) => setCreateBody(e.target.value)}
                placeholder="Write your document..."
                rows={10}
                className="min-h-40 resize-y"
                disabled={creating}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="library-create-folder">Folder</Label>
              <LibraryFolderSelect
                id="library-create-folder"
                folders={folders}
                value={createFolderId}
                onChange={setCreateFolderId}
                disabled={creating}
              />
            </div>
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setCreateOpen(false)}
              disabled={creating}
            >
              Cancel
            </Button>
            <Button
              type="button"
              onClick={() => void submitCreateDocument()}
              disabled={creating || !createTitle.trim()}
            >
              {creating ? <Loader2 className="size-4 animate-spin" /> : null}
              Create Document
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={uploadOpen} onOpenChange={setUploadOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Upload File</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="library-upload-folder">Folder</Label>
              <LibraryFolderSelect
                id="library-upload-folder"
                folders={folders}
                value={uploadFolderId}
                onChange={setUploadFolderId}
                disabled={uploading}
              />
            </div>
            <p className="text-sm text-muted-foreground">
              Choose one or more supported files. They will be added to the selected folder.
            </p>
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setUploadOpen(false)}
              disabled={uploading}
            >
              Cancel
            </Button>
            <Button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={uploading}
            >
              {uploading ? <Loader2 className="size-4 animate-spin" /> : <Upload className="size-4" />}
              Choose files
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={moveItemTarget !== null}
        onOpenChange={(open) => {
          if (!open) setMoveItemTarget(null);
        }}
      >
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Move to folder</DialogTitle>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="library-move-folder">Folder</Label>
            <LibraryFolderSelect
              id="library-move-folder"
              folders={folders}
              value={moveFolderId}
              onChange={setMoveFolderId}
              disabled={moving}
            />
            {moveItemTarget ? (
              <p className="text-sm text-muted-foreground">
                Moving “{moveItemTarget.title}”
              </p>
            ) : null}
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setMoveItemTarget(null)}
              disabled={moving}
            >
              Cancel
            </Button>
            <Button type="button" onClick={() => void submitMoveItem()} disabled={moving}>
              {moving ? <Loader2 className="size-4 animate-spin" /> : null}
              Move
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </AppShell>
  );
}

function SideLink({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex w-full rounded-md px-2 py-1.5 text-left",
        active ? "bg-primary/10 font-medium text-primary" : "hover:bg-muted",
      )}
    >
      {children}
    </button>
  );
}

function FolderTree({
  node,
  depth,
  activeId,
  onSelect,
  onAddChild,
}: {
  node: LibraryFolderNode;
  depth: number;
  activeId: string | null;
  onSelect: (id: string) => void;
  onAddChild: (id: string) => void;
}) {
  return (
    <div>
      <div
        className={cn(
          "group flex items-center gap-1 rounded-md pr-1",
          activeId === node.id ? "bg-primary/10 text-primary" : "hover:bg-muted",
        )}
        style={{ paddingLeft: 8 + depth * 12 }}
      >
        <button
          type="button"
          onClick={() => onSelect(node.id)}
          className="flex min-w-0 flex-1 items-center gap-1.5 py-1.5 text-left text-sm"
        >
          <Folder className="size-3.5 shrink-0" />
          <span className="truncate">{node.name}</span>
        </button>
        <button
          type="button"
          className="rounded p-1 opacity-0 group-hover:opacity-100 hover:bg-background"
          title="New subfolder"
          onClick={() => onAddChild(node.id)}
        >
          <FolderPlus className="size-3" />
        </button>
      </div>
      {node.children.map((child) => (
        <FolderTree
          key={child.id}
          node={child}
          depth={depth + 1}
          activeId={activeId}
          onSelect={onSelect}
          onAddChild={onAddChild}
        />
      ))}
    </div>
  );
}

function ItemIcon({ item }: { item: ApiLibraryItem }) {
  const className = "mt-0.5 size-5 shrink-0 text-muted-foreground";
  if (item.item_type === "document") return <FileText className={className} />;
  const name = (item.original_filename || item.title || "").toLowerCase();
  if (name.endsWith(".xlsx") || name.endsWith(".csv")) {
    return <FileSpreadsheet className={className} />;
  }
  return <File className={className} />;
}
