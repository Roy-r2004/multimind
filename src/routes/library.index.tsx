import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Loader2, Plus, Search, Upload } from "lucide-react";
import { toast } from "sonner";
import { AppShell } from "@/components/AppShell";
import { GlassCard } from "@/components/cinematic/PageChrome";
import { LibraryBreadcrumb } from "@/components/library/LibraryBreadcrumb";
import { LibraryDocumentEditor } from "@/components/library/LibraryDocumentEditor";
import { LibraryFolderSelect } from "@/components/library/LibraryFolderSelect";
import { LibraryHome } from "@/components/library/LibraryHome";
import { LibraryItemRow } from "@/components/library/LibraryItemRow";
import { LibrarySidebar } from "@/components/library/LibrarySidebar";
import { Button } from "@/components/ui/button";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";
import type { ApiLibraryFolder, ApiLibraryItem, ApiLibraryLabel } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";
import { COMPOSER_FILE_ACCEPT } from "@/lib/composerAttachments";
import { attachLibraryItemViaApi } from "@/lib/libraryAttach";
import {
  LIBRARY_HOME_RECENT_LIMIT,
  buildLibraryFolderTree,
  libraryFolderPath,
  libraryViewAfterFolderDelete,
  libraryViewHeading,
  type LibraryViewMode,
} from "@/lib/libraryUi";
import { useChatStore } from "@/lib/store";

export const Route = createFileRoute("/library/")({
  head: () => ({ meta: [{ title: "Library — MultiAI" }] }),
  component: LibraryPage,
});

function LibraryPage() {
  const { authHeaders } = useAuth();
  const navigate = useNavigate();
  const { activeChatId, createChat, setActiveChatId } = useChatStore();
  const retainRef = useRef<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [folders, setFolders] = useState<ApiLibraryFolder[]>([]);
  const [labels, setLabels] = useState<ApiLibraryLabel[]>([]);
  const [items, setItems] = useState<ApiLibraryItem[]>([]);
  const [view, setView] = useState<LibraryViewMode>({ kind: "home" });
  const [query, setQuery] = useState("");
  const [folderQuery, setFolderQuery] = useState("");
  const [foldersExpanded, setFoldersExpanded] = useState(false);
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

  const [renameFolderTarget, setRenameFolderTarget] = useState<ApiLibraryFolder | null>(null);
  const [renameFolderName, setRenameFolderName] = useState("");
  const [renamingFolder, setRenamingFolder] = useState(false);
  const [deleteFolderTarget, setDeleteFolderTarget] = useState<ApiLibraryFolder | null>(null);
  const [deletingFolder, setDeletingFolder] = useState(false);

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
      const params: Parameters<typeof api.library.listItems>[1] = {};
      if (view.kind === "home" || view.kind === "recent") params.recent = true;
      if (view.kind !== "home") {
        params.q = query.trim() || undefined;
        if (fileTypeFilter === "document") params.item_type = "document";
        if (fileTypeFilter === "file") params.item_type = "file";
      }
      if (view.kind === "favorites") params.favorites = true;
      if (view.kind === "folder") params.folder_id = view.folderId;
      if (view.kind === "unfiled") params.unfiled = true;
      if (view.kind === "label") params.label_id = view.labelId;

      let list = await api.library.listItems(auth, params);
      if (
        view.kind !== "home" &&
        fileTypeFilter &&
        fileTypeFilter !== "document" &&
        fileTypeFilter !== "file"
      ) {
        const ext = fileTypeFilter.toLowerCase();
        list = list.filter((item) =>
          (item.original_filename || item.title || "").toLowerCase().endsWith(ext),
        );
      }
      if (view.kind === "home") {
        list = list.slice(0, LIBRARY_HOME_RECENT_LIMIT);
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

  function openRenameFolder(folder: ApiLibraryFolder) {
    setRenameFolderTarget(folder);
    setRenameFolderName(folder.name);
  }

  async function submitRenameFolder() {
    const auth = authHeaders();
    const name = renameFolderName.trim();
    if (!auth || !renameFolderTarget || renamingFolder) return;
    if (!name) {
      toast.error("Folder name is required");
      return;
    }
    setRenamingFolder(true);
    try {
      await api.library.updateFolder(auth, renameFolderTarget.id, { name });
      await reloadMeta();
      setRenameFolderTarget(null);
      setRenameFolderName("");
      toast.success("Folder renamed");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not rename folder");
    } finally {
      setRenamingFolder(false);
    }
  }

  async function submitDeleteFolder() {
    const auth = authHeaders();
    if (!auth || !deleteFolderTarget || deletingFolder) return;
    const target = deleteFolderTarget;
    setDeletingFolder(true);
    try {
      await api.library.deleteFolder(auth, target.id);
      setView((current) => libraryViewAfterFolderDelete(current, target));
      await reloadMeta();
      setDeleteFolderTarget(null);
      toast.success("Folder deleted");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not delete folder");
      setDeleteFolderTarget(null);
    } finally {
      setDeletingFolder(false);
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

  const folderPath = view.kind === "folder" ? libraryFolderPath(folders, view.folderId) : [];
  const heading = libraryViewHeading(view, folders, labels);
  const itemCountLabel = `${items.length} ${items.length === 1 ? "item" : "items"}`;

  const itemRowHandlers = {
    onToggleFavorite: (item: ApiLibraryItem) => void toggleFavorite(item),
    onRename: (item: ApiLibraryItem) => void renameItem(item),
    onMove: openMoveItem,
    onDownload: (item: ApiLibraryItem) => void downloadItem(item),
    onAttach: (item: ApiLibraryItem) => void attachToChat(item),
    onDelete: (item: ApiLibraryItem) => void deleteItem(item),
  };

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

        <div className="grid items-start gap-6 lg:grid-cols-[240px_minmax(0,1fr)]">
          <GlassCard className="flex max-h-[min(70vh,calc(100vh-10rem))] flex-col overflow-hidden p-3 lg:sticky lg:top-4">
            <LibrarySidebar
              view={view}
              folders={folders}
              folderTree={folderTree}
              labels={labels}
              folderQuery={folderQuery}
              foldersExpanded={foldersExpanded}
              onFolderQueryChange={setFolderQuery}
              onToggleFoldersExpanded={() => setFoldersExpanded((open) => !open)}
              onView={setView}
              onSelectFolder={(folderId) => setView({ kind: "folder", folderId })}
              onCreateRootFolder={() => void createFolder(null)}
              onAddChild={(id) => void createFolder(id)}
              onRename={openRenameFolder}
              onDelete={setDeleteFolderTarget}
              onCreateLabel={() => {
                void (async () => {
                  const auth = authHeaders();
                  if (!auth) return;
                  const name = window.prompt("Label name");
                  if (!name?.trim()) return;
                  try {
                    await api.library.createLabel(auth, name.trim());
                    await reloadMeta();
                  } catch (error) {
                    toast.error(error instanceof Error ? error.message : "Could not create label");
                  }
                })();
              }}
            />
          </GlassCard>

          <div className="min-w-0 space-y-3">
            <LibraryBreadcrumb
              view={view}
              folderPath={folderPath}
              labelName={
                view.kind === "label"
                  ? labels.find((label) => label.id === view.labelId)?.name
                  : undefined
              }
              onHome={() => setView({ kind: "home" })}
              onFolder={(folderId) => setView({ kind: "folder", folderId })}
              onView={(kind) => setView({ kind })}
            />

            {view.kind === "home" ? (
              loading ? (
                <div className="flex items-center justify-center gap-2 py-16 text-muted-foreground">
                  <Loader2 className="size-4 animate-spin" />
                  Loading…
                </div>
              ) : (
                <LibraryHome
                  recentItems={items}
                  folders={folderTree}
                  attachingId={attachingId}
                  onOpenFolder={(folderId) => setView({ kind: "folder", folderId })}
                  {...itemRowHandlers}
                />
              )
            ) : (
              <>
                <div className="min-w-0">
                  <h2 className="truncate text-xl font-semibold tracking-tight">{heading}</h2>
                  {!loading && (
                    <p className="mt-0.5 text-xs text-muted-foreground">{itemCountLabel}</p>
                  )}
                </div>

                <div className="flex flex-wrap gap-3">
                  <div className="relative min-w-[180px] flex-1">
                    <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
                    <input
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                      placeholder={
                        view.kind === "folder"
                          ? "Search this folder..."
                          : "Search title, filename, labels, content…"
                      }
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
                        <LibraryItemRow
                          key={item.id}
                          item={item}
                          attaching={attachingId === item.id}
                          {...itemRowHandlers}
                        />
                      ))}
                    </ul>
                  )}
                </GlassCard>
              </>
            )}
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
              <LibraryDocumentEditor
                id="library-create-body"
                value={createBody}
                onChange={setCreateBody}
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
              {uploading ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Upload className="size-4" />
              )}
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
              <p className="text-sm text-muted-foreground">Moving “{moveItemTarget.title}”</p>
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

      <Dialog
        open={renameFolderTarget !== null}
        onOpenChange={(open) => {
          if (!open && !renamingFolder) {
            setRenameFolderTarget(null);
            setRenameFolderName("");
          }
        }}
      >
        <DialogContent className="max-w-md">
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void submitRenameFolder();
            }}
          >
            <DialogHeader>
              <DialogTitle>Rename folder</DialogTitle>
            </DialogHeader>
            <div className="py-4">
              <Label htmlFor="library-folder-rename">Folder name</Label>
              <Input
                id="library-folder-rename"
                value={renameFolderName}
                onChange={(event) => setRenameFolderName(event.target.value)}
                autoFocus
                disabled={renamingFolder}
                className="mt-2"
              />
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                disabled={renamingFolder}
                onClick={() => {
                  setRenameFolderTarget(null);
                  setRenameFolderName("");
                }}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={renamingFolder || !renameFolderName.trim()}>
                {renamingFolder ? <Loader2 className="size-4 animate-spin" /> : null}
                Save
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <AlertDialog
        open={deleteFolderTarget !== null}
        onOpenChange={(open) => {
          if (!open && !deletingFolder) setDeleteFolderTarget(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete folder?</AlertDialogTitle>
            <AlertDialogDescription>
              Delete “{deleteFolderTarget?.name}”? Only empty folders can be deleted. If this folder
              contains files, documents, or subfolders, move or delete them first.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deletingFolder}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={deletingFolder}
              onClick={(event) => {
                event.preventDefault();
                void submitDeleteFolder();
              }}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {deletingFolder ? <Loader2 className="size-4 animate-spin" /> : null}
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </AppShell>
  );
}
