import { useMemo, useState } from "react";
import { ChevronDown, FolderPlus, Search } from "lucide-react";
import type { ApiLibraryFolder } from "@/lib/api/types";
import {
  filterFolderTreeByQuery,
  folderIdsToExpandInTree,
  libraryExpandedPathIds,
  type LibraryFolderNode,
} from "@/lib/libraryUi";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { FolderTree } from "@/components/library/FolderTree";

/** Compact, searchable folder navigator — replaces the old permanent sidebar's folder tree. */
export function LibraryFoldersMenu({
  folderTree,
  folders,
  activeFolderId,
  activeFolderName,
  folderQuery,
  onFolderQueryChange,
  onSelectFolder,
  onCreateRootFolder,
  onAddChild,
  onRename,
  onDelete,
}: {
  folderTree: LibraryFolderNode[];
  folders: ApiLibraryFolder[];
  activeFolderId: string | null;
  activeFolderName: string | null;
  folderQuery: string;
  onFolderQueryChange: (value: string) => void;
  onSelectFolder: (folderId: string) => void;
  onCreateRootFolder: () => void;
  onAddChild: (folderId: string) => void;
  onRename: (folder: ApiLibraryFolder) => void;
  onDelete: (folder: ApiLibraryFolder) => void;
}) {
  const [open, setOpen] = useState(false);
  const searching = folderQuery.trim().length > 0;
  const filteredTree = useMemo(
    () => filterFolderTreeByQuery(folderTree, folderQuery),
    [folderTree, folderQuery],
  );
  const visibleRoots = searching ? filteredTree : folderTree;
  const forcedExpandedIds = useMemo(() => {
    const ids = new Set(libraryExpandedPathIds(folders, activeFolderId));
    if (searching) {
      for (const id of folderIdsToExpandInTree(filteredTree)) ids.add(id);
    }
    return ids;
  }, [folders, activeFolderId, searching, filteredTree]);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button type="button" variant="outline" className="gap-1.5">
          <span className="max-w-32 truncate">{activeFolderName ?? "Folders"}</span>
          <ChevronDown className="size-3.5 shrink-0 text-muted-foreground" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-72 p-3">
        <div className="mb-2 flex items-center justify-between">
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
        <div className="relative mb-2">
          <Search className="pointer-events-none absolute top-1/2 left-2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <input
            value={folderQuery}
            onChange={(event) => onFolderQueryChange(event.target.value)}
            placeholder="Search folders..."
            autoComplete="off"
            className="w-full rounded-md border border-border bg-background/70 py-1.5 pr-2 pl-7 text-xs outline-none focus:ring-2 focus:ring-ring"
          />
        </div>
        <div className="max-h-72 overflow-y-auto">
          {visibleRoots.length === 0 ? (
            <p className="px-2 py-4 text-center text-xs text-muted-foreground">
              {searching ? "No matching folders" : "No folders yet"}
            </p>
          ) : (
            <FolderTree
              nodes={visibleRoots}
              activeId={activeFolderId}
              forcedExpandedIds={forcedExpandedIds}
              collapseUserExpanded={!searching}
              onSelect={(id) => {
                onSelectFolder(id);
                setOpen(false);
              }}
              onAddChild={onAddChild}
              onRename={onRename}
              onDelete={onDelete}
            />
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}
