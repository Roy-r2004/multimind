/** Display helpers for Library UI. */

import type { ApiLibraryFolder, ApiLibraryItem } from "./api/types.ts";

export function formatLibraryBytes(size: number | null | undefined): string {
  if (size == null || size < 0) return "";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function libraryItemTypeLabel(item: ApiLibraryItem): string {
  if (item.item_type === "document") return "MultiMind Document";
  const name = (item.original_filename || item.title || "").toLowerCase();
  if (name.endsWith(".pdf")) return "PDF";
  if (name.endsWith(".docx")) return "Word";
  if (name.endsWith(".xlsx")) return "Excel";
  if (name.endsWith(".txt") || name.endsWith(".md")) return "Text";
  if (name.endsWith(".csv")) return "CSV";
  return "File";
}

export function formatLibraryUpdatedAt(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: date.getFullYear() !== new Date().getFullYear() ? "numeric" : undefined,
  });
}

export type LibraryFolderNode = ApiLibraryFolder & { children: LibraryFolderNode[] };

export type LibraryViewMode =
  | { kind: "home" }
  | { kind: "all" }
  | { kind: "favorites" }
  | { kind: "recent" }
  | { kind: "folder"; folderId: string }
  | { kind: "unfiled" }
  | { kind: "label"; labelId: string };

export const LIBRARY_SIDEBAR_FOLDER_LIMIT = 7;
export const LIBRARY_HOME_RECENT_LIMIT = 5;

/** Preserve an unrelated view, or leave a deleted active folder for its parent/root. */
export function libraryViewAfterFolderDelete(
  current: LibraryViewMode,
  deleted: Pick<ApiLibraryFolder, "id" | "parent_id">,
): LibraryViewMode {
  if (current.kind !== "folder" || current.folderId !== deleted.id) return current;
  return deleted.parent_id ? { kind: "folder", folderId: deleted.parent_id } : { kind: "home" };
}

export function buildLibraryFolderTree(folders: ApiLibraryFolder[]): LibraryFolderNode[] {
  const byId = new Map<string, LibraryFolderNode>();
  for (const folder of folders) {
    byId.set(folder.id, { ...folder, children: [] });
  }
  const roots: LibraryFolderNode[] = [];
  for (const node of byId.values()) {
    if (node.parent_id && byId.has(node.parent_id)) {
      byId.get(node.parent_id)!.children.push(node);
    } else {
      roots.push(node);
    }
  }
  const sortRecursive = (nodes: LibraryFolderNode[]) => {
    nodes.sort((a, b) => a.name.localeCompare(b.name));
    for (const node of nodes) sortRecursive(node.children);
  };
  sortRecursive(roots);
  return roots;
}

export function libraryFolderPath(
  folders: ApiLibraryFolder[],
  folderId: string | null,
): ApiLibraryFolder[] {
  if (!folderId) return [];
  const byId = new Map(folders.map((f) => [f.id, f]));
  const path: ApiLibraryFolder[] = [];
  let current: ApiLibraryFolder | undefined = byId.get(folderId);
  const guard = new Set<string>();
  while (current && !guard.has(current.id)) {
    guard.add(current.id);
    path.unshift(current);
    current = current.parent_id ? byId.get(current.parent_id) : undefined;
  }
  return path;
}

export type LibraryFolderOption = {
  id: string;
  label: string;
  depth: number;
};

/** Depth-first folder options with path labels for dropdowns. */
export function flattenLibraryFolderOptions(folders: ApiLibraryFolder[]): LibraryFolderOption[] {
  const tree = buildLibraryFolderTree(folders);
  const options: LibraryFolderOption[] = [];

  const walk = (nodes: LibraryFolderNode[], ancestors: string[]) => {
    for (const node of nodes) {
      const pathNames = [...ancestors, node.name];
      options.push({
        id: node.id,
        label: pathNames.join(" / "),
        depth: ancestors.length,
      });
      walk(node.children, pathNames);
    }
  };

  walk(tree, []);
  return options;
}

export function libraryTopLevelFolderId(
  folders: ApiLibraryFolder[],
  folderId: string | null,
): string | null {
  const path = libraryFolderPath(folders, folderId);
  return path[0]?.id ?? null;
}

/** Folder ids on the active path, including the selected folder. */
export function libraryExpandedPathIds(
  folders: ApiLibraryFolder[],
  folderId: string | null,
): string[] {
  return libraryFolderPath(folders, folderId).map((folder) => folder.id);
}

export function visibleTopLevelFolders(
  roots: LibraryFolderNode[],
  options: {
    showAll: boolean;
    limit?: number;
    pinnedTopLevelId?: string | null;
  },
): { nodes: LibraryFolderNode[]; hiddenCount: number } {
  const limit = options.limit ?? LIBRARY_SIDEBAR_FOLDER_LIMIT;
  if (options.showAll || roots.length <= limit) {
    return { nodes: roots, hiddenCount: 0 };
  }

  const keep = new Set(roots.slice(0, limit).map((node) => node.id));
  if (options.pinnedTopLevelId) keep.add(options.pinnedTopLevelId);

  const nodes = roots.filter((node) => keep.has(node.id));
  return { nodes, hiddenCount: roots.length - nodes.length };
}

export function filterFolderTreeByQuery(
  nodes: LibraryFolderNode[],
  query: string,
): LibraryFolderNode[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return nodes;

  const walk = (list: LibraryFolderNode[]): LibraryFolderNode[] => {
    const result: LibraryFolderNode[] = [];
    for (const node of list) {
      const selfMatch = node.name.toLowerCase().includes(needle);
      const childMatches = walk(node.children);
      if (selfMatch) {
        result.push(node);
      } else if (childMatches.length > 0) {
        result.push({ ...node, children: childMatches });
      }
    }
    return result;
  };

  return walk(nodes);
}

/** Expand every ancestor so search matches stay visible in the tree. */
export function folderIdsToExpandInTree(nodes: LibraryFolderNode[]): string[] {
  const ids: string[] = [];
  const walk = (list: LibraryFolderNode[]) => {
    for (const node of list) {
      if (node.children.length > 0) {
        ids.push(node.id);
        walk(node.children);
      }
    }
  };
  walk(nodes);
  return ids;
}

export function libraryViewHeading(
  view: LibraryViewMode,
  folders: ApiLibraryFolder[],
  labels: { id: string; name: string }[] = [],
): string {
  if (view.kind === "home") return "Library";
  if (view.kind === "all") return "All Items";
  if (view.kind === "favorites") return "Favorites";
  if (view.kind === "recent") return "Recent";
  if (view.kind === "unfiled") return "Unfiled";
  if (view.kind === "label") {
    return labels.find((label) => label.id === view.labelId)?.name ?? "Label";
  }
  const current = folders.find((folder) => folder.id === view.folderId);
  return current?.name ?? "Folder";
}
