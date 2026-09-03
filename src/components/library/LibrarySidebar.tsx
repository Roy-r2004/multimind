import { useEffect, useMemo, type ReactNode } from "react";
import { ChevronDown, ChevronUp, FolderPlus, Plus, Search } from "lucide-react";
import type { ApiLibraryFolder, ApiLibraryLabel } from "@/lib/api/types";
import {
  LIBRARY_SIDEBAR_FOLDER_LIMIT,
  filterFolderTreeByQuery,
  folderIdsToExpandInTree,
  libraryExpandedPathIds,
  libraryTopLevelFolderId,
  visibleTopLevelFolders,
  type LibraryFolderNode,
  type LibraryViewMode,
} from "@/lib/libraryUi";
import { cn } from "@/lib/utils";
import { FolderTree } from "@/components/library/FolderTree";

export function LibrarySidebar({
  view,
  folders,
  folderTree,
  labels,
  folderQuery,
  foldersExpanded,
  onFolderQueryChange,
  onToggleFoldersExpanded,
  onView,
  onSelectFolder,
  onCreateRootFolder,
  onAddChild,
  onRename,
  onDelete,
  onCreateLabel,
}: {
  view: LibraryViewMode;
  folders: ApiLibraryFolder[];
  folderTree: LibraryFolderNode[];
  labels: ApiLibraryLabel[];
  folderQuery: string;
  foldersExpanded: boolean;
  onFolderQueryChange: (value: string) => void;
  onToggleFoldersExpanded: () => void;
  onView: (view: LibraryViewMode) => void;
  onSelectFolder: (folderId: string) => void;
  onCreateRootFolder: () => void;
  onAddChild: (folderId: string) => void;
  onRename: (folder: ApiLibraryFolder) => void;
  onDelete: (folder: ApiLibraryFolder) => void;
  onCreateLabel: () => void;
}) {
  const activeFolderId = view.kind === "folder" ? view.folderId : null;
  const searching = folderQuery.trim().length > 0;

  const filteredTree = useMemo(
    () => filterFolderTreeByQuery(folderTree, folderQuery),
    [folderTree, folderQuery],
  );

  const { nodes: visibleRoots, hiddenCount } = useMemo(() => {
    if (searching) {
      return { nodes: filteredTree, hiddenCount: 0 };
    }
    return visibleTopLevelFolders(folderTree, {
      showAll: foldersExpanded,
      limit: LIBRARY_SIDEBAR_FOLDER_LIMIT,
      pinnedTopLevelId: libraryTopLevelFolderId(folders, activeFolderId),
    });
  }, [searching, filteredTree, folderTree, foldersExpanded, folders, activeFolderId]);

  const forcedExpandedIds = useMemo(() => {
    const ids = new Set(libraryExpandedPathIds(folders, activeFolderId));
    if (searching) {
      for (const id of folderIdsToExpandInTree(filteredTree)) ids.add(id);
    }
    return ids;
  }, [folders, activeFolderId, searching, filteredTree]);

  useEffect(() => {
    const el = document.querySelector("[data-library-folder-active='true']");
    el?.scrollIntoView({ block: "nearest" });
  }, [activeFolderId, visibleRoots, searching, foldersExpanded]);

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      <nav className="shrink-0 space-y-0.5 text-sm">
        <SideLink active={view.kind === "all"} onClick={() => onView({ kind: "all" })}>
          All Items
        </SideLink>
        <SideLink active={view.kind === "favorites"} onClick={() => onView({ kind: "favorites" })}>
          Favorites
        </SideLink>
        <SideLink active={view.kind === "recent"} onClick={() => onView({ kind: "recent" })}>
          Recent
        </SideLink>
        <SideLink active={view.kind === "unfiled"} onClick={() => onView({ kind: "unfiled" })}>
          Unfiled
        </SideLink>
      </nav>

      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div className="mb-2 flex shrink-0 items-center justify-between">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Folders
          </p>
          <button
            type="button"
            className="rounded p-1 text-muted-foreground hover:bg-muted"
            title="New root folder"
            onClick={onCreateRootFolder}
          >
            <FolderPlus className="size-3.5" />
          </button>
        </div>
        <div className="relative mb-2 shrink-0">
          <Search className="pointer-events-none absolute top-1/2 left-2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <input
            value={folderQuery}
            onChange={(event) => onFolderQueryChange(event.target.value)}
            placeholder="Search folders..."
            autoComplete="off"
            className="w-full rounded-md border border-border bg-background/70 py-1.5 pr-2 pl-7 text-xs outline-none focus:ring-2 focus:ring-ring"
          />
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {visibleRoots.length === 0 ? (
            <p className="px-2 text-xs text-muted-foreground">
              {searching ? "No matching folders" : "No folders yet"}
            </p>
          ) : (
            <FolderTree
              nodes={visibleRoots}
              activeId={activeFolderId}
              forcedExpandedIds={forcedExpandedIds}
              collapseUserExpanded={!searching}
              onSelect={onSelectFolder}
              onAddChild={onAddChild}
              onRename={onRename}
              onDelete={onDelete}
            />
          )}
          {!searching && hiddenCount > 0 && (
            <button
              type="button"
              onClick={onToggleFoldersExpanded}
              className="mt-1 flex w-full items-center justify-center gap-1 rounded-md px-2 py-1.5 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
            >
              Show more
              <ChevronDown className="size-3.5" />
            </button>
          )}
          {!searching && foldersExpanded && folderTree.length > LIBRARY_SIDEBAR_FOLDER_LIMIT && (
            <button
              type="button"
              onClick={onToggleFoldersExpanded}
              className="mt-1 flex w-full items-center justify-center gap-1 rounded-md px-2 py-1.5 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
            >
              Show less
              <ChevronUp className="size-3.5" />
            </button>
          )}
        </div>
      </div>

      <div className="shrink-0 border-t border-border/80 pt-3">
        <div className="mb-2 flex items-center justify-between">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Labels
          </p>
          <button
            type="button"
            className="rounded p-1 text-muted-foreground hover:bg-muted"
            title="New label"
            onClick={onCreateLabel}
          >
            <Plus className="size-3.5" />
          </button>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {labels.map((label) => (
            <button
              key={label.id}
              type="button"
              onClick={() => onView({ kind: "label", labelId: label.id })}
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
          {labels.length === 0 && <p className="text-xs text-muted-foreground">No labels yet</p>}
        </div>
      </div>
    </div>
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
