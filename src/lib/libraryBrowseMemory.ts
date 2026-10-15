/** Library browsing context that survives opening a document. */

import { LIBRARY_HOME_RECENT_LIMIT, type LibraryViewMode } from "./libraryUi.ts";

export const LIBRARY_BROWSE_STORAGE_KEY = "multimind.library.browse";

export type LibraryBrowseState = {
  view: LibraryViewMode;
  query: string;
  fileTypeFilter: string;
  folderQuery: string;
  foldersExpanded: boolean;
  scrollY: number;
};

export type LibraryListQuery = {
  recent?: boolean;
  q?: string;
  item_type?: "document" | "file";
  favorites?: boolean;
  folder_id?: string;
  unfiled?: boolean;
  label_id?: string;
  extension?: string;
  homeLimit?: number;
};

export const EMPTY_LIBRARY_BROWSE: LibraryBrowseState = {
  view: { kind: "home" },
  query: "",
  fileTypeFilter: "",
  folderQuery: "",
  foldersExpanded: false,
  scrollY: 0,
};

const VIEW_KINDS = new Set(["home", "all", "favorites", "recent", "unfiled", "folder", "label"]);

export function libraryViewsEqual(a: LibraryViewMode, b: LibraryViewMode): boolean {
  if (a.kind !== b.kind) return false;
  if (a.kind === "folder" && b.kind === "folder") return a.folderId === b.folderId;
  if (a.kind === "label" && b.kind === "label") return a.labelId === b.labelId;
  return true;
}

export function libraryBrowseContextKey(
  state: Pick<LibraryBrowseState, "view" | "query" | "fileTypeFilter">,
): string {
  return JSON.stringify({
    view: state.view,
    query: state.query,
    fileTypeFilter: state.fileTypeFilter,
  });
}

function isLibraryView(value: unknown): value is LibraryViewMode {
  if (!value || typeof value !== "object") return false;
  const kind = (value as { kind?: unknown }).kind;
  if (typeof kind !== "string" || !VIEW_KINDS.has(kind)) return false;
  if (kind === "folder") {
    return typeof (value as { folderId?: unknown }).folderId === "string";
  }
  if (kind === "label") {
    return typeof (value as { labelId?: unknown }).labelId === "string";
  }
  return kind === "home" || kind === "all" || kind === "favorites" || kind === "recent" || kind === "unfiled";
}

function finiteScroll(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) && value > 0
    ? Math.min(value, 1_000_000)
    : 0;
}

export function parseLibraryBrowseState(raw: unknown): LibraryBrowseState {
  if (!raw || typeof raw !== "object") return { ...EMPTY_LIBRARY_BROWSE };
  const record = raw as Record<string, unknown>;
  const view = isLibraryView(record.view) ? record.view : EMPTY_LIBRARY_BROWSE.view;
  return {
    view,
    query: typeof record.query === "string" ? record.query : "",
    fileTypeFilter: typeof record.fileTypeFilter === "string" ? record.fileTypeFilter : "",
    folderQuery: typeof record.folderQuery === "string" ? record.folderQuery : "",
    foldersExpanded: record.foldersExpanded === true,
    scrollY: finiteScroll(record.scrollY),
  };
}

export function readLibraryBrowseState(storage: Storage | null | undefined): LibraryBrowseState {
  if (!storage) return { ...EMPTY_LIBRARY_BROWSE };
  try {
    const raw = storage.getItem(LIBRARY_BROWSE_STORAGE_KEY);
    if (!raw) return { ...EMPTY_LIBRARY_BROWSE };
    return parseLibraryBrowseState(JSON.parse(raw));
  } catch {
    return { ...EMPTY_LIBRARY_BROWSE };
  }
}

export function writeLibraryBrowseState(
  storage: Storage | null | undefined,
  state: LibraryBrowseState,
): void {
  if (!storage) return;
  try {
    storage.setItem(LIBRARY_BROWSE_STORAGE_KEY, JSON.stringify(state));
  } catch {
    // Private mode or a full quota should not block Library navigation.
  }
}

export function browserSessionStorage(): Storage | null {
  try {
    if (typeof window === "undefined") return null;
    return window.sessionStorage;
  } catch {
    return null;
  }
}

/** Replace browsing context. A new section, search, or filter drops the old scroll. */
export function applyLibraryBrowseChange(
  current: LibraryBrowseState,
  patch: Partial<LibraryBrowseState>,
): LibraryBrowseState {
  const next: LibraryBrowseState = { ...current, ...patch };
  const contextChanged = libraryBrowseContextKey(current) !== libraryBrowseContextKey(next);
  if (contextChanged && patch.scrollY === undefined) next.scrollY = 0;
  return next;
}

export function rememberLibraryScroll(
  current: LibraryBrowseState,
  scrollY: number,
): LibraryBrowseState {
  const nextScroll = finiteScroll(scrollY);
  if (current.scrollY === nextScroll) return current;
  return { ...current, scrollY: nextScroll };
}

/** Scroll is applied once, after the restored list is ready, so the page does not jump early. */
export function libraryScrollToRestore(
  scrollY: number,
  options: { contentReady: boolean; alreadyRestored: boolean },
): number | null {
  if (!options.contentReady || options.alreadyRestored) return null;
  return scrollY > 0 ? scrollY : null;
}

export function libraryListQuery(
  state: Pick<LibraryBrowseState, "view" | "query" | "fileTypeFilter">,
): LibraryListQuery {
  const { view, query, fileTypeFilter } = state;
  const params: LibraryListQuery = {};
  if (view.kind === "home" || view.kind === "recent") params.recent = true;
  if (view.kind !== "home") {
    const q = query.trim();
    if (q) params.q = q;
    if (fileTypeFilter === "document") params.item_type = "document";
    if (fileTypeFilter === "file") params.item_type = "file";
    if (fileTypeFilter && fileTypeFilter !== "document" && fileTypeFilter !== "file") {
      params.extension = fileTypeFilter.toLowerCase();
    }
  }
  if (view.kind === "favorites") params.favorites = true;
  if (view.kind === "folder") params.folder_id = view.folderId;
  if (view.kind === "unfiled") params.unfiled = true;
  if (view.kind === "label") params.label_id = view.labelId;
  if (view.kind === "home") params.homeLimit = LIBRARY_HOME_RECENT_LIMIT;
  return params;
}

export function readLibraryScrollTop(): number {
  if (typeof window === "undefined") return 0;
  const doc = document.scrollingElement ?? document.documentElement;
  return doc?.scrollTop || window.scrollY || 0;
}

export function writeLibraryScrollTop(top: number): void {
  if (typeof window === "undefined") return;
  const y = Number.isFinite(top) ? Math.max(0, top) : 0;
  const doc = document.scrollingElement ?? document.documentElement;
  if (doc) doc.scrollTop = y;
  window.scrollTo(0, y);
}
