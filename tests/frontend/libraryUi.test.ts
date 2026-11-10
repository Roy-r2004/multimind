import assert from "node:assert/strict";
import test from "node:test";

import type { ApiLibraryFolder } from "../../src/lib/api/types.ts";
import {
  buildLibraryFolderTree,
  filterFolderTreeByQuery,
  folderIdsToExpandInTree,
  libraryActiveFilterChips,
  libraryAdjacentItemId,
  libraryExpandedPathIds,
  libraryFileTypeFilterLabel,
  libraryNavLabel,
  libraryTopLevelFolderId,
  libraryViewAfterFolderDelete,
  LIBRARY_FOLDER_NAME_MAX_LENGTH,
  LIBRARY_NAV_ITEMS,
  validateLibraryFolderName,
  visibleTopLevelFolders,
} from "../../src/lib/libraryUi.ts";

function folder(id: string, name: string, parent_id: string | null = null): ApiLibraryFolder {
  return {
    id,
    name,
    parent_id,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

test("deleting the active nested folder selects its parent", () => {
  assert.deepEqual(
    libraryViewAfterFolderDelete(
      { kind: "folder", folderId: "child" },
      { id: "child", parent_id: "parent" },
    ),
    { kind: "folder", folderId: "parent" },
  );
});

test("deleting the active root folder returns to All Items", () => {
  assert.deepEqual(
    libraryViewAfterFolderDelete(
      { kind: "folder", folderId: "root" },
      { id: "root", parent_id: null },
    ),
    { kind: "all" },
  );
});

test("LIBRARY_NAV_ITEMS lists exactly the 4 primary nav options", () => {
  assert.deepEqual(
    LIBRARY_NAV_ITEMS.map((item) => item.kind),
    ["all", "favorites", "recent", "unfiled"],
  );
});

test("libraryNavLabel resolves the 4 nav kinds and falls back for folder/label views", () => {
  assert.equal(libraryNavLabel({ kind: "all" }), "All Items");
  assert.equal(libraryNavLabel({ kind: "favorites" }), "Favorites");
  assert.equal(libraryNavLabel({ kind: "recent" }), "Recent");
  assert.equal(libraryNavLabel({ kind: "unfiled" }), "Unfiled");
  assert.equal(libraryNavLabel({ kind: "folder", folderId: "f1" }), "All Items");
  assert.equal(libraryNavLabel({ kind: "label", labelId: "l1" }), "All Items");
});

test("deleting an inactive folder preserves the current view", () => {
  const current = { kind: "folder" as const, folderId: "active" };
  assert.equal(libraryViewAfterFolderDelete(current, { id: "other", parent_id: null }), current);
});

test("compact sidebar shows seven top-level folders and pins the active branch", () => {
  const roots = buildLibraryFolderTree(
    Array.from({ length: 10 }, (_, i) => folder(`f${i}`, `Folder ${i}`)),
  );
  const compact = visibleTopLevelFolders(roots, { showAll: false, limit: 7 });
  assert.equal(compact.nodes.length, 7);
  assert.equal(compact.hiddenCount, 3);
  assert.equal(compact.nodes.at(-1)?.id, "f6");

  const pinned = visibleTopLevelFolders(roots, {
    showAll: false,
    limit: 7,
    pinnedTopLevelId: "f9",
  });
  assert.equal(pinned.nodes.length, 8);
  assert.ok(pinned.nodes.some((node) => node.id === "f9"));
  assert.equal(pinned.hiddenCount, 2);

  const expanded = visibleTopLevelFolders(roots, { showAll: true, limit: 7 });
  assert.equal(expanded.nodes.length, 10);
  assert.equal(expanded.hiddenCount, 0);
});

test("folder search matches nested names and keeps parent context", () => {
  const tree = buildLibraryFolderTree([
    folder("alc", "Alcohol"),
    folder("alc-pac", "PAC", "alc"),
    folder("fent", "Fentanyl"),
    folder("ex", "Exercise"),
    folder("fit", "Fitness", "ex"),
    folder("pac", "PAC", "fit"),
  ]);

  const fent = filterFolderTreeByQuery(tree, "fent");
  assert.deepEqual(
    fent.map((node) => node.id),
    ["fent"],
  );

  const pac = filterFolderTreeByQuery(tree, "pac");
  assert.equal(pac.length, 2);
  assert.equal(pac[0]?.id, "alc");
  assert.deepEqual(
    pac[0]?.children.map((child) => child.id),
    ["alc-pac"],
  );
  assert.equal(pac[1]?.id, "ex");
  assert.deepEqual(
    pac[1]?.children.map((child) => child.id),
    ["fit"],
  );
  assert.deepEqual(
    pac[1]?.children[0]?.children.map((child) => child.id),
    ["pac"],
  );
  assert.deepEqual(folderIdsToExpandInTree(pac).sort(), ["alc", "ex", "fit"]);
});

test("active nested folder expands its ancestor ids", () => {
  const folders = [
    folder("ex", "Exercise"),
    folder("fit", "Fitness", "ex"),
    folder("pac", "PAC", "fit"),
  ];
  assert.equal(libraryTopLevelFolderId(folders, "pac"), "ex");
  assert.deepEqual(libraryExpandedPathIds(folders, "pac"), ["ex", "fit", "pac"]);
});

test("validateLibraryFolderName rejects empty or whitespace-only names", () => {
  assert.equal(validateLibraryFolderName(""), "Folder name is required");
  assert.equal(validateLibraryFolderName("   "), "Folder name is required");
});

test("validateLibraryFolderName accepts a trimmed, in-range name", () => {
  assert.equal(validateLibraryFolderName("  Research  "), null);
});

test("validateLibraryFolderName enforces the backend's max length", () => {
  const atLimit = "a".repeat(LIBRARY_FOLDER_NAME_MAX_LENGTH);
  const overLimit = "a".repeat(LIBRARY_FOLDER_NAME_MAX_LENGTH + 1);
  assert.equal(validateLibraryFolderName(atLimit), null);
  assert.equal(
    validateLibraryFolderName(overLimit),
    `Folder name must be ${LIBRARY_FOLDER_NAME_MAX_LENGTH} characters or fewer`,
  );
});

test("libraryFileTypeFilterLabel maps known values and passes through extensions", () => {
  assert.equal(libraryFileTypeFilterLabel("document"), "MultiMind Document");
  assert.equal(libraryFileTypeFilterLabel("file"), "Uploaded files");
  assert.equal(libraryFileTypeFilterLabel(".pdf"), ".PDF");
  assert.equal(libraryFileTypeFilterLabel(""), "");
});

test("libraryActiveFilterChips is empty when nothing but the defaults is active", () => {
  assert.deepEqual(
    libraryActiveFilterChips({ query: "  ", fileTypeFilter: "", sort: "updated_desc" }),
    [],
  );
});

test("libraryActiveFilterChips describes each active filter independently", () => {
  const chips = libraryActiveFilterChips({
    query: "austria",
    fileTypeFilter: "document",
    sort: "name_asc",
  });
  assert.deepEqual(chips, [
    { key: "query", label: 'Search: "austria"' },
    { key: "fileType", label: "Type: MultiMind Document" },
    { key: "sort", label: "Sort: Name A–Z" },
  ]);
});

test("libraryAdjacentItemId moves within the list and stops at either end", () => {
  const ids = ["a", "b", "c"];
  assert.equal(libraryAdjacentItemId(ids, "a", "next"), "b");
  assert.equal(libraryAdjacentItemId(ids, "b", "next"), "c");
  assert.equal(libraryAdjacentItemId(ids, "c", "next"), null);
  assert.equal(libraryAdjacentItemId(ids, "c", "prev"), "b");
  assert.equal(libraryAdjacentItemId(ids, "a", "prev"), null);
});

test("libraryAdjacentItemId returns null when the current id is not in the list", () => {
  assert.equal(libraryAdjacentItemId(["a", "b"], "missing", "next"), null);
});
