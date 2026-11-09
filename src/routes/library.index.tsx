import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { MouseEvent as ReactMouseEvent } from "react";
import { Loader2, Plus, Search, Tag, Upload, X } from "lucide-react";
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
import { Checkbox } from "@/components/ui/checkbox";
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";
import type { ApiLibraryFolder, ApiLibraryItem, ApiLibraryLabel } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";
import { COMPOSER_FILE_ACCEPT } from "@/lib/composerAttachments";
import { attachLibraryItemViaApi } from "@/lib/libraryAttach";
import {
  addLabelToIds,
  bulkResultMessage,
  intersectSelection,
  LIBRARY_BULK_CONCURRENCY_LIMIT,
  mapWithConcurrencyLimit,
  removeLabelFromIds,
  selectAll,
  selectRange,
  summarizeBulkResults,
  toggleSelection,
} from "@/lib/libraryBulkActions";
import {
  buildLibraryFolderTree,
  libraryActiveFilterChips,
  libraryFolderPath,
  libraryViewAfterFolderDelete,
  libraryViewHeading,
  validateLibraryFolderName,
  LIBRARY_DEFAULT_SORT,
  LIBRARY_SORT_OPTIONS,
} from "@/lib/libraryUi";
import {
  applyLibraryBrowseChange,
  browserSessionStorage,
  EMPTY_LIBRARY_BROWSE,
  libraryBrowseContextKey,
  libraryListQuery,
  libraryScrollToRestore,
  readLibraryBrowseState,
  readLibraryScrollTop,
  rememberLibraryScroll,
  writeLibraryBrowseState,
  writeLibraryScrollTop,
  type LibraryBrowseState,
} from "@/lib/libraryBrowseMemory";
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
  const [browse, setBrowse] = useState<LibraryBrowseState>(EMPTY_LIBRARY_BROWSE);
  const [browseReady, setBrowseReady] = useState(false);
  const browseRef = useRef(browse);
  browseRef.current = browse;
  const scrollRestoredRef = useRef(false);
  const pendingScrollRef = useRef(0);
  const leaveLibraryRef = useRef(false);
  const ignoreScrollRef = useRef(false);
  const [loading, setLoading] = useState(true);
  const view = browse.view;
  const query = browse.query;
  const folderQuery = browse.folderQuery;
  const foldersExpanded = browse.foldersExpanded;
  const fileTypeFilter = browse.fileTypeFilter;
  const sort = browse.sort;

  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const lastSelectedIndexRef = useRef<number | null>(null);
  // Bumped on every reloadItems() call so a reload that's superseded by a
  // newer one (e.g. the user switched folders while a bulk action's own
  // reload was still in flight) can detect it's stale and skip applying.
  const reloadGenerationRef = useRef(0);

  const clearSelection = useCallback(() => {
    setSelectedIds(new Set());
    lastSelectedIndexRef.current = null;
  }, []);

  const updateBrowse = useCallback(
    (
      patch:
        | Partial<LibraryBrowseState>
        | ((current: LibraryBrowseState) => Partial<LibraryBrowseState>),
    ) => {
      const current = browseRef.current;
      const resolved = typeof patch === "function" ? patch(current) : patch;
      const next = applyLibraryBrowseChange(current, resolved);
      browseRef.current = next;
      writeLibraryBrowseState(browserSessionStorage(), next);
      if (libraryBrowseContextKey(current) !== libraryBrowseContextKey(next)) {
        pendingScrollRef.current = 0;
        ignoreScrollRef.current = true;
        writeLibraryScrollTop(0);
        window.requestAnimationFrame(() => {
          ignoreScrollRef.current = false;
        });
        // Folder/search/filter/sort changes mean the visible rows are about to
        // change: a stale selection could otherwise end up acting on items the
        // user never intended once the new list loads.
        clearSelection();
      }
      setBrowse(next);
    },
    [clearSelection],
  );

  const flushLibraryScroll = useCallback(() => {
    if (leaveLibraryRef.current || ignoreScrollRef.current) return;
    const next = rememberLibraryScroll(browseRef.current, readLibraryScrollTop());
    if (next === browseRef.current) return;
    browseRef.current = next;
    writeLibraryBrowseState(browserSessionStorage(), next);
  }, []);

  const rememberBeforeDocument = useCallback(() => {
    flushLibraryScroll();
    leaveLibraryRef.current = true;
  }, [flushLibraryScroll]);

  useLayoutEffect(() => {
    const stored = readLibraryBrowseState(browserSessionStorage());
    pendingScrollRef.current = stored.scrollY;
    browseRef.current = stored;
    setBrowse(stored);
    setBrowseReady(true);
  }, []);
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

  const [createFolderOpen, setCreateFolderOpen] = useState(false);
  const [createFolderParentId, setCreateFolderParentId] = useState<string | null>(null);
  const [createFolderName, setCreateFolderName] = useState("");
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [createFolderError, setCreateFolderError] = useState<string | null>(null);

  const [renameFolderTarget, setRenameFolderTarget] = useState<ApiLibraryFolder | null>(null);
  const [renameFolderName, setRenameFolderName] = useState("");
  const [renamingFolder, setRenamingFolder] = useState(false);
  const [renameFolderError, setRenameFolderError] = useState<string | null>(null);
  const [deleteFolderTarget, setDeleteFolderTarget] = useState<ApiLibraryFolder | null>(null);
  const [deletingFolder, setDeletingFolder] = useState(false);
  const [deleteFolderError, setDeleteFolderError] = useState<string | null>(null);

  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkMoveOpen, setBulkMoveOpen] = useState(false);
  const [bulkMoveFolderId, setBulkMoveFolderId] = useState<string | null>(null);
  const [bulkDeleteOpen, setBulkDeleteOpen] = useState(false);

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
    const generation = ++reloadGenerationRef.current;
    setLoading(true);
    try {
      const listQuery = libraryListQuery({ view, query, fileTypeFilter, sort });
      const params: Parameters<typeof api.library.listItems>[1] = {};
      if (listQuery.recent) params.recent = true;
      if (listQuery.q) params.q = listQuery.q;
      if (listQuery.item_type) params.item_type = listQuery.item_type;
      if (listQuery.favorites) params.favorites = true;
      if (listQuery.folder_id) params.folder_id = listQuery.folder_id;
      if (listQuery.unfiled) params.unfiled = true;
      if (listQuery.label_id) params.label_id = listQuery.label_id;
      if (listQuery.sort) params.sort = listQuery.sort;

      let list = await api.library.listItems(auth, params);
      if (listQuery.extension) {
        const ext = listQuery.extension;
        list = list.filter((item) =>
          (item.original_filename || item.title || "").toLowerCase().endsWith(ext),
        );
      }
      if (listQuery.homeLimit) {
        list = list.slice(0, listQuery.homeLimit);
      }
      // A newer reload (folder/search/filter/sort change, or another bulk
      // action) started after this one — applying this response now would
      // overwrite more current data and could resurrect a stale selection.
      if (reloadGenerationRef.current !== generation) return;
      setItems(list);
      // Drop any selected id that fell out of view (moved, deleted, filtered out)
      // instead of letting a bulk action silently act on it later.
      setSelectedIds((prev) =>
        intersectSelection(
          prev,
          list.map((item) => item.id),
        ),
      );
    } catch (error) {
      if (reloadGenerationRef.current !== generation) return;
      toast.error(error instanceof Error ? error.message : "Failed to load Library");
    } finally {
      if (reloadGenerationRef.current === generation) setLoading(false);
    }
  }, [authHeaders, query, view, fileTypeFilter, sort]);

  useEffect(() => {
    if (!browseReady) return;
    void reloadMeta();
  }, [browseReady, reloadMeta]);

  useEffect(() => {
    if (!browseReady) return;
    void reloadItems();
  }, [browseReady, reloadItems]);

  useEffect(() => {
    if (!browseReady) return;
    const flush = () => flushLibraryScroll();
    window.addEventListener("scroll", flush, { passive: true });
    return () => {
      window.removeEventListener("scroll", flush);
    };
  }, [browseReady, flushLibraryScroll]);

  useEffect(() => {
    if (!browseReady || loading || scrollRestoredRef.current) return;
    scrollRestoredRef.current = true;
    const top = libraryScrollToRestore(pendingScrollRef.current, {
      contentReady: true,
      alreadyRestored: false,
    });
    if (top == null) return;
    writeLibraryScrollTop(top);
    window.requestAnimationFrame(() => writeLibraryScrollTop(top));
  }, [browseReady, loading, items.length]);

  function openCreateFolder(parentId: string | null) {
    setCreateFolderParentId(parentId);
    setCreateFolderName("");
    setCreateFolderError(null);
    setCreateFolderOpen(true);
  }

  async function submitCreateFolder() {
    const auth = authHeaders();
    if (!auth || creatingFolder) return;
    const validationError = validateLibraryFolderName(createFolderName);
    if (validationError) {
      setCreateFolderError(validationError);
      return;
    }
    setCreatingFolder(true);
    setCreateFolderError(null);
    try {
      await api.library.createFolder(auth, {
        name: createFolderName.trim(),
        parent_id: createFolderParentId,
      });
      await reloadMeta();
      setCreateFolderOpen(false);
      setCreateFolderName("");
      toast.success("Folder created");
    } catch (error) {
      setCreateFolderError(error instanceof Error ? error.message : "Could not create folder");
    } finally {
      setCreatingFolder(false);
    }
  }

  function openRenameFolder(folder: ApiLibraryFolder) {
    setRenameFolderTarget(folder);
    setRenameFolderName(folder.name);
    setRenameFolderError(null);
  }

  async function submitRenameFolder() {
    const auth = authHeaders();
    if (!auth || !renameFolderTarget || renamingFolder) return;
    const validationError = validateLibraryFolderName(renameFolderName);
    if (validationError) {
      setRenameFolderError(validationError);
      return;
    }
    setRenamingFolder(true);
    setRenameFolderError(null);
    try {
      await api.library.updateFolder(auth, renameFolderTarget.id, {
        name: renameFolderName.trim(),
      });
      await reloadMeta();
      setRenameFolderTarget(null);
      setRenameFolderName("");
      toast.success("Folder renamed");
    } catch (error) {
      setRenameFolderError(error instanceof Error ? error.message : "Could not rename folder");
    } finally {
      setRenamingFolder(false);
    }
  }

  function openDeleteFolder(folder: ApiLibraryFolder) {
    setDeleteFolderTarget(folder);
    setDeleteFolderError(null);
  }

  async function submitDeleteFolder() {
    const auth = authHeaders();
    if (!auth || !deleteFolderTarget || deletingFolder) return;
    const target = deleteFolderTarget;
    setDeletingFolder(true);
    setDeleteFolderError(null);
    try {
      await api.library.deleteFolder(auth, target.id);
      updateBrowse((current) => ({
        view: libraryViewAfterFolderDelete(current.view, target),
      }));
      await reloadMeta();
      setDeleteFolderTarget(null);
      toast.success("Folder deleted");
    } catch (error) {
      setDeleteFolderError(error instanceof Error ? error.message : "Could not delete folder");
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

  function handleToggleItemSelect(item: ApiLibraryItem, index: number, event: ReactMouseEvent) {
    if (event.shiftKey && lastSelectedIndexRef.current != null) {
      setSelectedIds((prev) =>
        selectRange(
          prev,
          items.map((row) => row.id),
          lastSelectedIndexRef.current ?? index,
          index,
        ),
      );
    } else {
      setSelectedIds((prev) => toggleSelection(prev, item.id));
    }
    lastSelectedIndexRef.current = index;
  }

  function selectAllVisible() {
    setSelectedIds(selectAll(items.map((item) => item.id)));
  }

  /** Runs `action` over every selected id, tolerating per-item failures, and reports accurate counts. */
  async function runBulkAction(
    label: string,
    ids: string[],
    action: (id: string) => Promise<unknown>,
  ): Promise<void> {
    if (bulkBusy || ids.length === 0) return;
    setBulkBusy(true);
    try {
      // Cap concurrent requests instead of firing one per selected item at
      // once — a large "select all" could otherwise launch hundreds of
      // simultaneous API calls.
      const results = await mapWithConcurrencyLimit(ids, LIBRARY_BULK_CONCURRENCY_LIMIT, (id) =>
        action(id),
      );
      const outcome = summarizeBulkResults(
        ids.map((id, index) => ({ id, ok: results[index]?.status === "fulfilled" })),
      );
      const { level, text } = bulkResultMessage(label, outcome);
      if (level === "success") toast.success(text);
      else toast.error(text);
      // Keep only the failures selected so the user can see and retry exactly
      // what didn't work; successes are done and drop out of the selection.
      setSelectedIds(new Set(outcome.failedIds));
      await reloadItems();
    } finally {
      setBulkBusy(false);
    }
  }

  async function bulkMove() {
    const auth = authHeaders();
    if (!auth) return;
    const ids = [...selectedIds];
    await runBulkAction("Moved", ids, (id) =>
      api.library.updateItem(auth, id, {
        folder_id: bulkMoveFolderId ?? undefined,
        clear_folder: !bulkMoveFolderId,
      }),
    );
    setBulkMoveOpen(false);
  }

  async function bulkSetFavorite(value: boolean) {
    const auth = authHeaders();
    if (!auth) return;
    const ids = [...selectedIds];
    await runBulkAction(value ? "Favorited" : "Unfavorited", ids, (id) =>
      api.library.updateItem(auth, id, { is_favorite: value }),
    );
  }

  async function bulkAddLabel(labelId: string) {
    const auth = authHeaders();
    if (!auth) return;
    const ids = [...selectedIds];
    const byId = new Map(items.map((item) => [item.id, item]));
    await runBulkAction("Labeled", ids, (id) => {
      const current = byId.get(id)?.labels.map((label) => label.id) ?? [];
      return api.library.updateItem(auth, id, { label_ids: addLabelToIds(current, labelId) });
    });
  }

  async function bulkRemoveLabel(labelId: string) {
    const auth = authHeaders();
    if (!auth) return;
    const ids = [...selectedIds];
    const byId = new Map(items.map((item) => [item.id, item]));
    await runBulkAction("Updated labels for", ids, (id) => {
      const current = byId.get(id)?.labels.map((label) => label.id) ?? [];
      return api.library.updateItem(auth, id, {
        label_ids: removeLabelFromIds(current, labelId),
      });
    });
  }

  async function bulkDelete() {
    const auth = authHeaders();
    if (!auth) return;
    const ids = [...selectedIds];
    await runBulkAction("Deleted", ids, (id) => api.library.deleteItem(auth, id));
    setBulkDeleteOpen(false);
  }

  const folderPath = view.kind === "folder" ? libraryFolderPath(folders, view.folderId) : [];
  const heading = libraryViewHeading(view, folders, labels);
  const itemCountLabel = `${items.length} ${items.length === 1 ? "item" : "items"}`;
  const activeFilterChips = libraryActiveFilterChips({ query, fileTypeFilter, sort });
  const allVisibleSelected = items.length > 0 && items.every((item) => selectedIds.has(item.id));
  const someVisibleSelected = selectedIds.size > 0 && !allVisibleSelected;

  function clearActiveFilterChip(key: "query" | "fileType" | "sort") {
    if (key === "query") updateBrowse({ query: "" });
    else if (key === "fileType") updateBrowse({ fileTypeFilter: "" });
    else updateBrowse({ sort: LIBRARY_DEFAULT_SORT });
  }

  const itemRowHandlers = {
    onToggleFavorite: (item: ApiLibraryItem) => void toggleFavorite(item),
    onRename: (item: ApiLibraryItem) => void renameItem(item),
    onMove: openMoveItem,
    onDownload: (item: ApiLibraryItem) => void downloadItem(item),
    onAttach: (item: ApiLibraryItem) => void attachToChat(item),
    onDelete: (item: ApiLibraryItem) => void deleteItem(item),
  };

  if (!browseReady) {
    return (
      <AppShell>
        <div className="flex items-center justify-center gap-2 py-24 text-muted-foreground">
          <Loader2 className="size-4 animate-spin" />
          Loading…
        </div>
      </AppShell>
    );
  }

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
              onFolderQueryChange={(value) => updateBrowse({ folderQuery: value })}
              onToggleFoldersExpanded={() =>
                updateBrowse((current) => ({ foldersExpanded: !current.foldersExpanded }))
              }
              onView={(next) => updateBrowse({ view: next })}
              onSelectFolder={(folderId) => updateBrowse({ view: { kind: "folder", folderId } })}
              onCreateRootFolder={() => openCreateFolder(null)}
              onAddChild={(id) => openCreateFolder(id)}
              onRename={openRenameFolder}
              onDelete={openDeleteFolder}
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
              onHome={() => updateBrowse({ view: { kind: "home" } })}
              onFolder={(folderId) => updateBrowse({ view: { kind: "folder", folderId } })}
              onView={(kind) => updateBrowse({ view: { kind } })}
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
                  onOpenFolder={(folderId) => updateBrowse({ view: { kind: "folder", folderId } })}
                  onOpenItem={rememberBeforeDocument}
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
                      onChange={(e) => updateBrowse({ query: e.target.value })}
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
                    onChange={(e) => updateBrowse({ fileTypeFilter: e.target.value })}
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
                  <select
                    value={sort}
                    onChange={(e) =>
                      updateBrowse({ sort: e.target.value as LibraryBrowseState["sort"] })
                    }
                    className="rounded-lg border border-border bg-background/70 px-3 py-2 text-sm"
                    aria-label="Sort Library items"
                  >
                    {LIBRARY_SORT_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </div>

                {activeFilterChips.length > 0 && (
                  <div className="flex flex-wrap items-center gap-2">
                    {activeFilterChips.map((chip) => (
                      <button
                        key={chip.key}
                        type="button"
                        onClick={() => clearActiveFilterChip(chip.key)}
                        className="inline-flex items-center gap-1 rounded-full border border-border bg-muted/40 px-2.5 py-1 text-xs text-muted-foreground hover:bg-muted"
                        title={`Clear: ${chip.label}`}
                      >
                        {chip.label}
                        <X className="size-3" />
                      </button>
                    ))}
                    <button
                      type="button"
                      onClick={() =>
                        updateBrowse({ query: "", fileTypeFilter: "", sort: LIBRARY_DEFAULT_SORT })
                      }
                      className="text-xs text-muted-foreground underline-offset-2 hover:underline"
                    >
                      Clear all filters
                    </button>
                  </div>
                )}

                {selectedIds.size > 0 && (
                  <div className="flex flex-wrap items-center gap-2 rounded-lg border border-primary/30 bg-primary/5 px-3 py-2 text-sm">
                    <span className="font-medium">{selectedIds.size} selected</span>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={bulkBusy}
                      onClick={clearSelection}
                    >
                      Clear
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={bulkBusy}
                      onClick={() => {
                        setBulkMoveFolderId(null);
                        setBulkMoveOpen(true);
                      }}
                    >
                      Move
                    </Button>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={bulkBusy || labels.length === 0}
                        >
                          <Tag className="size-4" />
                          Add label
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="start">
                        {labels.map((label) => (
                          <DropdownMenuItem
                            key={label.id}
                            onSelect={() => void bulkAddLabel(label.id)}
                          >
                            {label.name}
                          </DropdownMenuItem>
                        ))}
                      </DropdownMenuContent>
                    </DropdownMenu>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={bulkBusy || labels.length === 0}
                        >
                          <Tag className="size-4" />
                          Remove label
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="start">
                        {labels.map((label) => (
                          <DropdownMenuItem
                            key={label.id}
                            onSelect={() => void bulkRemoveLabel(label.id)}
                          >
                            {label.name}
                          </DropdownMenuItem>
                        ))}
                      </DropdownMenuContent>
                    </DropdownMenu>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={bulkBusy}
                      onClick={() => void bulkSetFavorite(true)}
                    >
                      Favorite
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={bulkBusy}
                      onClick={() => void bulkSetFavorite(false)}
                    >
                      Unfavorite
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={bulkBusy}
                      onClick={() => setBulkDeleteOpen(true)}
                      className="text-destructive hover:text-destructive"
                    >
                      Delete
                    </Button>
                    {bulkBusy && <Loader2 className="size-4 animate-spin text-muted-foreground" />}
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
                    <>
                      <div className="flex items-center gap-3 border-b border-border px-4 py-2 text-xs text-muted-foreground">
                        <Checkbox
                          checked={
                            allVisibleSelected
                              ? true
                              : someVisibleSelected
                                ? "indeterminate"
                                : false
                          }
                          disabled={bulkBusy}
                          onClick={() => {
                            if (allVisibleSelected) clearSelection();
                            else selectAllVisible();
                          }}
                          aria-label={
                            allVisibleSelected
                              ? "Deselect all visible items"
                              : "Select all visible items"
                          }
                        />
                        <span>
                          {allVisibleSelected
                            ? `All ${items.length} selected`
                            : `Select all ${items.length} visible`}
                        </span>
                      </div>
                      <ul className="divide-y divide-border">
                        {items.map((item, index) => (
                          <LibraryItemRow
                            key={item.id}
                            item={item}
                            attaching={attachingId === item.id}
                            onOpen={rememberBeforeDocument}
                            selected={selectedIds.has(item.id)}
                            selectDisabled={bulkBusy}
                            onToggleSelect={(event) => handleToggleItemSelect(item, index, event)}
                            {...itemRowHandlers}
                          />
                        ))}
                      </ul>
                    </>
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
        open={createFolderOpen}
        onOpenChange={(open) => {
          if (!open && !creatingFolder) {
            setCreateFolderOpen(false);
            setCreateFolderName("");
            setCreateFolderError(null);
          }
        }}
      >
        <DialogContent className="max-w-md">
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void submitCreateFolder();
            }}
          >
            <DialogHeader>
              <DialogTitle>New folder</DialogTitle>
            </DialogHeader>
            <div className="py-4">
              <Label htmlFor="library-folder-create">Folder name</Label>
              <Input
                id="library-folder-create"
                value={createFolderName}
                onChange={(event) => {
                  setCreateFolderName(event.target.value);
                  if (createFolderError) setCreateFolderError(null);
                }}
                autoFocus
                disabled={creatingFolder}
                aria-invalid={createFolderError ? true : undefined}
                aria-describedby={createFolderError ? "library-folder-create-error" : undefined}
                className="mt-2"
              />
              {createFolderError ? (
                <p id="library-folder-create-error" className="mt-2 text-sm text-destructive">
                  {createFolderError}
                </p>
              ) : null}
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                disabled={creatingFolder}
                onClick={() => {
                  setCreateFolderOpen(false);
                  setCreateFolderName("");
                  setCreateFolderError(null);
                }}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={creatingFolder || !createFolderName.trim()}>
                {creatingFolder ? <Loader2 className="size-4 animate-spin" /> : null}
                Create
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog
        open={renameFolderTarget !== null}
        onOpenChange={(open) => {
          if (!open && !renamingFolder) {
            setRenameFolderTarget(null);
            setRenameFolderName("");
            setRenameFolderError(null);
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
                onChange={(event) => {
                  setRenameFolderName(event.target.value);
                  if (renameFolderError) setRenameFolderError(null);
                }}
                autoFocus
                disabled={renamingFolder}
                aria-invalid={renameFolderError ? true : undefined}
                aria-describedby={renameFolderError ? "library-folder-rename-error" : undefined}
                className="mt-2"
              />
              {renameFolderError ? (
                <p id="library-folder-rename-error" className="mt-2 text-sm text-destructive">
                  {renameFolderError}
                </p>
              ) : null}
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                disabled={renamingFolder}
                onClick={() => {
                  setRenameFolderTarget(null);
                  setRenameFolderName("");
                  setRenameFolderError(null);
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
          if (!open && !deletingFolder) {
            setDeleteFolderTarget(null);
            setDeleteFolderError(null);
          }
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
          {deleteFolderError ? (
            <p className="text-sm text-destructive">{deleteFolderError}</p>
          ) : null}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deletingFolder} onClick={() => setDeleteFolderError(null)}>
              Cancel
            </AlertDialogCancel>
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

      <Dialog
        open={bulkMoveOpen}
        onOpenChange={(open) => {
          if (!open && !bulkBusy) setBulkMoveOpen(false);
        }}
      >
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Move {selectedIds.size} items to folder</DialogTitle>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="library-bulk-move-folder">Folder</Label>
            <LibraryFolderSelect
              id="library-bulk-move-folder"
              folders={folders}
              value={bulkMoveFolderId}
              onChange={setBulkMoveFolderId}
              disabled={bulkBusy}
            />
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setBulkMoveOpen(false)}
              disabled={bulkBusy}
            >
              Cancel
            </Button>
            <Button type="button" onClick={() => void bulkMove()} disabled={bulkBusy}>
              {bulkBusy ? <Loader2 className="size-4 animate-spin" /> : null}
              Move
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog
        open={bulkDeleteOpen}
        onOpenChange={(open) => {
          if (!open && !bulkBusy) setBulkDeleteOpen(false);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {selectedIds.size} items?</AlertDialogTitle>
            <AlertDialogDescription>
              This permanently deletes {selectedIds.size}{" "}
              {selectedIds.size === 1 ? "item" : "items"}. Library does not currently support a
              trash or restore — this action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={bulkBusy}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={bulkBusy}
              onClick={(event) => {
                event.preventDefault();
                void bulkDelete();
              }}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {bulkBusy ? <Loader2 className="size-4 animate-spin" /> : null}
              Delete permanently
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </AppShell>
  );
}
