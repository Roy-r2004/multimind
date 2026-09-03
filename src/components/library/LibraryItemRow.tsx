import { Link } from "@tanstack/react-router";
import {
  Download,
  File,
  FileSpreadsheet,
  FileText,
  FolderInput,
  Loader2,
  MoreHorizontal,
  Paperclip,
  Pencil,
  Star,
  Trash2,
} from "lucide-react";
import type { ApiLibraryItem } from "@/lib/api/types";
import { formatLibraryBytes, formatLibraryUpdatedAt, libraryItemTypeLabel } from "@/lib/libraryUi";
import { cn } from "@/lib/utils";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export function LibraryItemRow({
  item,
  attaching,
  onToggleFavorite,
  onRename,
  onMove,
  onDownload,
  onAttach,
  onDelete,
}: {
  item: ApiLibraryItem;
  attaching: boolean;
  onToggleFavorite: (item: ApiLibraryItem) => void;
  onRename: (item: ApiLibraryItem) => void;
  onMove: (item: ApiLibraryItem) => void;
  onDownload: (item: ApiLibraryItem) => void;
  onAttach: (item: ApiLibraryItem) => void;
  onDelete: (item: ApiLibraryItem) => void;
}) {
  const typeLabel = libraryItemTypeLabel(item);
  const size = item.size_bytes != null ? formatLibraryBytes(item.size_bytes) : "";
  const updated = formatLibraryUpdatedAt(item.updated_at);
  const filename =
    item.original_filename && item.original_filename !== item.title ? item.original_filename : "";
  const meta = [typeLabel, size || null, filename || null, updated ? `Updated ${updated}` : null]
    .filter(Boolean)
    .join(" · ");

  return (
    <li className="flex items-center gap-3 px-4 py-2.5 hover:bg-muted/40">
      <ItemIcon item={item} />
      <div className="min-w-0 flex-1 overflow-hidden">
        <Link
          to="/library/$itemId"
          params={{ itemId: item.id }}
          className="block truncate font-medium hover:underline"
          title={item.title}
        >
          {item.title}
        </Link>
        <p className="mt-0.5 truncate text-xs text-muted-foreground">{meta}</p>
        {item.labels.length > 0 && (
          <div className="mt-1 flex min-w-0 flex-wrap gap-1">
            {item.labels.map((label) => (
              <span
                key={label.id}
                className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground"
              >
                {label.name}
              </span>
            ))}
          </div>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-0.5">
        <button
          type="button"
          title={item.is_favorite ? "Unfavorite" : "Favorite"}
          onClick={() => onToggleFavorite(item)}
          className="rounded-md p-1.5 hover:bg-muted"
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
          title="Attach to Current Chat"
          disabled={attaching}
          onClick={() => onAttach(item)}
          className="inline-flex items-center gap-1.5 rounded-md border border-border px-2 py-1 text-xs hover:bg-muted disabled:opacity-50"
        >
          {attaching ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <Paperclip className="size-3.5" />
          )}
          <span className="hidden sm:inline">Attach to Chat</span>
          <span className="sm:hidden">Attach</span>
        </button>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              className="rounded-md p-1.5 text-muted-foreground hover:bg-muted"
              title="More actions"
              aria-label={`More actions for ${item.title}`}
            >
              <MoreHorizontal className="size-4" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-44">
            <DropdownMenuItem onSelect={() => onRename(item)}>
              <Pencil className="size-4" />
              Rename
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => onMove(item)}>
              <FolderInput className="size-4" />
              Move
            </DropdownMenuItem>
            {item.item_type === "file" && (
              <DropdownMenuItem onSelect={() => onDownload(item)}>
                <Download className="size-4" />
                Download
              </DropdownMenuItem>
            )}
            <DropdownMenuSeparator />
            <DropdownMenuItem
              onSelect={() => onDelete(item)}
              className="text-destructive focus:text-destructive"
            >
              <Trash2 className="size-4" />
              Delete
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </li>
  );
}

function ItemIcon({ item }: { item: ApiLibraryItem }) {
  const className = "size-5 shrink-0 text-muted-foreground";
  if (item.item_type === "document") return <FileText className={className} />;
  const name = (item.original_filename || item.title || "").toLowerCase();
  if (name.endsWith(".xlsx") || name.endsWith(".csv")) {
    return <FileSpreadsheet className={className} />;
  }
  return <File className={className} />;
}
