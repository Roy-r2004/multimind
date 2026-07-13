import { FolderPlus, MoreHorizontal, Pencil, Trash2 } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { ScrapingMissionSummary } from "@/lib/scraping/types";

type Props = {
  mission: ScrapingMissionSummary;
  onAddOrChangeProject: () => void;
  onRemoveProject: () => void;
  onRename: () => void;
  onDelete: () => void;
};

export function ScrapingMissionMenu({
  mission,
  onAddOrChangeProject,
  onRemoveProject,
  onRename,
  onDelete,
}: Props) {
  const runAfterMenuCloses = (action: () => void) => {
    window.setTimeout(action, 0);
  };

  return (
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label={`Scraping mission options for ${mission.title}`}
          onClick={(event) => {
            event.stopPropagation();
          }}
          className="rounded p-1 opacity-0 transition-opacity group-hover:opacity-100 focus:opacity-100 data-[state=open]:opacity-100"
        >
          <MoreHorizontal className="size-4 text-muted-foreground" />
        </button>
      </DropdownMenuTrigger>

      <DropdownMenuContent
        align="end"
        className="z-[100] w-44"
        onClick={(event) => event.stopPropagation()}
      >
        <DropdownMenuItem onSelect={() => runAfterMenuCloses(onAddOrChangeProject)}>
          <FolderPlus className="size-3.5" />
          {mission.project_id ? "Change Project" : "Add to Project"}
        </DropdownMenuItem>

        {mission.project_id && (
          <DropdownMenuItem onSelect={() => runAfterMenuCloses(onRemoveProject)}>
            Remove from Project
          </DropdownMenuItem>
        )}

        <DropdownMenuSeparator />

        <DropdownMenuItem onSelect={() => runAfterMenuCloses(onRename)}>
          <Pencil className="size-3.5" />
          Rename Mission
        </DropdownMenuItem>

        <DropdownMenuItem
          className="text-destructive focus:text-destructive"
          onSelect={() => runAfterMenuCloses(onDelete)}
        >
          <Trash2 className="size-3.5" />
          Delete Mission
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
