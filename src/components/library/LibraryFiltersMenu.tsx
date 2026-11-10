import { ChevronDown, Plus } from "lucide-react";
import type { ApiLibraryLabel } from "@/lib/api/types";
import { LIBRARY_SORT_OPTIONS, type LibrarySortOption } from "@/lib/libraryUi";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

/** One compact popover for sort, file type, and label filtering/creation. */
export function LibraryFiltersMenu({
  fileTypeFilter,
  onFileTypeFilterChange,
  sort,
  onSortChange,
  labels,
  activeLabelId,
  onSelectLabel,
  onCreateLabel,
  activeCount,
}: {
  fileTypeFilter: string;
  onFileTypeFilterChange: (value: string) => void;
  sort: LibrarySortOption;
  onSortChange: (value: LibrarySortOption) => void;
  labels: ApiLibraryLabel[];
  activeLabelId: string | null;
  onSelectLabel: (labelId: string) => void;
  onCreateLabel: () => void;
  activeCount: number;
}) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button type="button" variant="outline" className="gap-1.5">
          Filters
          {activeCount > 0 && (
            <span className="flex size-5 items-center justify-center rounded-full bg-primary/15 text-xs font-medium text-primary">
              {activeCount}
            </span>
          )}
          <ChevronDown className="size-3.5 shrink-0 text-muted-foreground" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-72 space-y-4 p-3">
        <div className="space-y-1.5">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Sort</p>
          <select
            value={sort}
            onChange={(event) => onSortChange(event.target.value as LibrarySortOption)}
            className="w-full rounded-md border border-border bg-background/70 px-2 py-1.5 text-sm"
            aria-label="Sort Library items"
          >
            {LIBRARY_SORT_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>

        <div className="space-y-1.5">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Type</p>
          <select
            value={fileTypeFilter}
            onChange={(event) => onFileTypeFilterChange(event.target.value)}
            className="w-full rounded-md border border-border bg-background/70 px-2 py-1.5 text-sm"
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

        <div className="space-y-1.5">
          <div className="flex items-center justify-between">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Labels
            </p>
            <button
              type="button"
              onClick={onCreateLabel}
              className="rounded p-1 text-muted-foreground hover:bg-muted"
              title="New label"
            >
              <Plus className="size-3.5" />
            </button>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {labels.map((label) => (
              <button
                key={label.id}
                type="button"
                onClick={() => onSelectLabel(label.id)}
                className={cn(
                  "rounded-full border px-2.5 py-0.5 text-xs",
                  activeLabelId === label.id
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
      </PopoverContent>
    </Popover>
  );
}
