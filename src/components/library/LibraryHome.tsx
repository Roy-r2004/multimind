import { Folder } from "lucide-react";
import type { ApiLibraryItem } from "@/lib/api/types";
import type { LibraryFolderNode } from "@/lib/libraryUi";
import { GlassCard } from "@/components/cinematic/PageChrome";
import { LibraryItemRow } from "@/components/library/LibraryItemRow";

export function LibraryHome({
  recentItems,
  folders,
  attachingId,
  onOpenFolder,
  onToggleFavorite,
  onRename,
  onMove,
  onDownload,
  onAttach,
  onDelete,
}: {
  recentItems: ApiLibraryItem[];
  folders: LibraryFolderNode[];
  attachingId: string | null;
  onOpenFolder: (folderId: string) => void;
  onToggleFavorite: (item: ApiLibraryItem) => void;
  onRename: (item: ApiLibraryItem) => void;
  onMove: (item: ApiLibraryItem) => void;
  onDownload: (item: ApiLibraryItem) => void;
  onAttach: (item: ApiLibraryItem) => void;
  onDelete: (item: ApiLibraryItem) => void;
}) {
  return (
    <div className="space-y-6">
      <section className="space-y-2">
        <h2 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Recent
        </h2>
        <GlassCard className="overflow-hidden">
          {recentItems.length === 0 ? (
            <p className="px-4 py-8 text-center text-sm text-muted-foreground">
              No recent items yet.
            </p>
          ) : (
            <ul className="divide-y divide-border">
              {recentItems.map((item) => (
                <LibraryItemRow
                  key={item.id}
                  item={item}
                  attaching={attachingId === item.id}
                  onToggleFavorite={onToggleFavorite}
                  onRename={onRename}
                  onMove={onMove}
                  onDownload={onDownload}
                  onAttach={onAttach}
                  onDelete={onDelete}
                />
              ))}
            </ul>
          )}
        </GlassCard>
      </section>

      <section className="space-y-2">
        <h2 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Folders
        </h2>
        {folders.length === 0 ? (
          <GlassCard className="px-4 py-8 text-center text-sm text-muted-foreground">
            No folders yet. Create a folder from the sidebar.
          </GlassCard>
        ) : (
          <div className="grid gap-2 sm:grid-cols-2">
            {folders.map((folder) => (
              <button
                key={folder.id}
                type="button"
                onClick={() => onOpenFolder(folder.id)}
                className="flex items-start gap-2.5 rounded-xl border border-border/90 bg-card/95 px-3 py-2.5 text-left hover:bg-muted/40"
              >
                <Folder className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                <span className="min-w-0">
                  <span className="block truncate text-sm font-medium">{folder.name}</span>
                </span>
              </button>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
