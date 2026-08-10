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
export function flattenLibraryFolderOptions(
  folders: ApiLibraryFolder[],
): LibraryFolderOption[] {
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
