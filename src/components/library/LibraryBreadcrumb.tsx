import { ChevronRight } from "lucide-react";
import type { ApiLibraryFolder } from "@/lib/api/types";
import type { LibraryViewMode } from "@/lib/libraryUi";
import { cn } from "@/lib/utils";

type Crumb = {
  key: string;
  label: string;
  onClick?: () => void;
};

export function LibraryBreadcrumb({
  view,
  folderPath,
  labelName,
  onHome,
  onFolder,
  onView,
  className,
}: {
  view: LibraryViewMode;
  folderPath: ApiLibraryFolder[];
  labelName?: string;
  onHome: () => void;
  onFolder: (folderId: string) => void;
  onView?: (kind: Exclude<LibraryViewMode["kind"], "folder" | "label" | "home">) => void;
  className?: string;
}) {
  const crumbs: Crumb[] = [{ key: "home", label: "Library", onClick: onHome }];

  if (view.kind === "folder") {
    for (const folder of folderPath) {
      crumbs.push({
        key: folder.id,
        label: folder.name,
        onClick: () => onFolder(folder.id),
      });
    }
  } else if (view.kind === "all") {
    crumbs.push({
      key: "all",
      label: "All Items",
      onClick: onView ? () => onView("all") : undefined,
    });
  } else if (view.kind === "favorites") {
    crumbs.push({
      key: "favorites",
      label: "Favorites",
      onClick: onView ? () => onView("favorites") : undefined,
    });
  } else if (view.kind === "recent") {
    crumbs.push({
      key: "recent",
      label: "Recent",
      onClick: onView ? () => onView("recent") : undefined,
    });
  } else if (view.kind === "unfiled") {
    crumbs.push({
      key: "unfiled",
      label: "Unfiled",
      onClick: onView ? () => onView("unfiled") : undefined,
    });
  } else if (view.kind === "label") {
    crumbs.push({ key: "label", label: labelName || "Label" });
  }

  return (
    <nav
      aria-label="Library breadcrumb"
      className={cn(
        "flex min-w-0 flex-wrap items-center gap-0.5 text-xs text-muted-foreground",
        className,
      )}
    >
      {crumbs.map((crumb, index) => {
        const isLast = index === crumbs.length - 1;
        return (
          <span key={crumb.key} className="flex min-w-0 items-center gap-0.5">
            {index > 0 && <ChevronRight className="size-3 shrink-0 text-muted-foreground/70" />}
            {crumb.onClick && !isLast ? (
              <button
                type="button"
                onClick={crumb.onClick}
                className="truncate rounded-sm px-0.5 hover:text-foreground"
              >
                {crumb.label}
              </button>
            ) : (
              <span className={cn("truncate px-0.5", isLast && "text-foreground")}>
                {crumb.label}
              </span>
            )}
          </span>
        );
      })}
    </nav>
  );
}
