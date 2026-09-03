import { useEffect, useMemo, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  Folder,
  FolderPlus,
  MoreHorizontal,
  Pencil,
  Trash2,
} from "lucide-react";
import type { ApiLibraryFolder } from "@/lib/api/types";
import type { LibraryFolderNode } from "@/lib/libraryUi";
import { cn } from "@/lib/utils";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export function FolderTree({
  nodes,
  activeId,
  forcedExpandedIds,
  collapseUserExpanded,
  onSelect,
  onAddChild,
  onRename,
  onDelete,
}: {
  nodes: LibraryFolderNode[];
  activeId: string | null;
  forcedExpandedIds: Iterable<string>;
  collapseUserExpanded?: boolean;
  onSelect: (id: string) => void;
  onAddChild: (id: string) => void;
  onRename: (folder: ApiLibraryFolder) => void;
  onDelete: (folder: ApiLibraryFolder) => void;
}) {
  const forced = useMemo(() => new Set(forcedExpandedIds), [forcedExpandedIds]);
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());

  useEffect(() => {
    if (collapseUserExpanded) setExpanded(new Set());
  }, [collapseUserExpanded]);

  function isExpanded(id: string) {
    return forced.has(id) || expanded.has(id);
  }

  function toggle(id: string) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <div>
      {nodes.map((node) => (
        <FolderTreeNode
          key={node.id}
          node={node}
          depth={0}
          activeId={activeId}
          isExpanded={isExpanded}
          onToggle={toggle}
          onSelect={onSelect}
          onAddChild={onAddChild}
          onRename={onRename}
          onDelete={onDelete}
        />
      ))}
    </div>
  );
}

function FolderTreeNode({
  node,
  depth,
  activeId,
  isExpanded,
  onToggle,
  onSelect,
  onAddChild,
  onRename,
  onDelete,
}: {
  node: LibraryFolderNode;
  depth: number;
  activeId: string | null;
  isExpanded: (id: string) => boolean;
  onToggle: (id: string) => void;
  onSelect: (id: string) => void;
  onAddChild: (id: string) => void;
  onRename: (folder: ApiLibraryFolder) => void;
  onDelete: (folder: ApiLibraryFolder) => void;
}) {
  const hasChildren = node.children.length > 0;
  const open = hasChildren && isExpanded(node.id);
  const active = activeId === node.id;

  return (
    <div>
      <div
        data-library-folder-active={active ? "true" : undefined}
        className={cn(
          "group flex items-center gap-0.5 rounded-md pr-1",
          active ? "bg-primary/10 font-medium text-primary" : "hover:bg-muted",
        )}
        style={{ paddingLeft: 4 + depth * 12 }}
      >
        <button
          type="button"
          disabled={!hasChildren}
          aria-expanded={hasChildren ? open : undefined}
          aria-label={
            hasChildren ? (open ? `Collapse ${node.name}` : `Expand ${node.name}`) : undefined
          }
          onClick={() => onToggle(node.id)}
          className={cn(
            "flex size-5 shrink-0 items-center justify-center rounded text-muted-foreground",
            hasChildren ? "hover:bg-background/80" : "invisible",
          )}
        >
          {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
        </button>
        <button
          type="button"
          onClick={() => onSelect(node.id)}
          className="flex min-w-0 flex-1 items-center gap-1.5 py-1 text-left text-sm"
          title={node.name}
        >
          <Folder className="size-3.5 shrink-0" />
          <span className="truncate">{node.name}</span>
        </button>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              className="rounded p-1 opacity-0 group-hover:opacity-100 hover:bg-background focus:opacity-100 data-[state=open]:opacity-100"
              title="Folder actions"
              aria-label={`Actions for ${node.name}`}
            >
              <MoreHorizontal className="size-3" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-36">
            <DropdownMenuItem onSelect={() => onRename(node)}>
              <Pencil className="size-4" />
              Rename
            </DropdownMenuItem>
            <DropdownMenuItem
              onSelect={() => onDelete(node)}
              className="text-destructive focus:text-destructive"
            >
              <Trash2 className="size-4" />
              Delete
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        <button
          type="button"
          className="rounded p-1 opacity-0 group-hover:opacity-100 hover:bg-background"
          title="New subfolder"
          onClick={() => onAddChild(node.id)}
        >
          <FolderPlus className="size-3" />
        </button>
      </div>
      {open &&
        node.children.map((child) => (
          <FolderTreeNode
            key={child.id}
            node={child}
            depth={depth + 1}
            activeId={activeId}
            isExpanded={isExpanded}
            onToggle={onToggle}
            onSelect={onSelect}
            onAddChild={onAddChild}
            onRename={onRename}
            onDelete={onDelete}
          />
        ))}
    </div>
  );
}
