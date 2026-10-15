import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import {
  applyLibraryBrowseChange,
  EMPTY_LIBRARY_BROWSE,
  libraryListQuery,
  libraryScrollToRestore,
  parseLibraryBrowseState,
  readLibraryBrowseState,
  writeLibraryBrowseState,
  type LibraryBrowseState,
} from "../../src/lib/libraryBrowseMemory.ts";

const root = join(dirname(fileURLToPath(import.meta.url)), "../..");
const librarySrc = readFileSync(join(root, "src/routes/library.index.tsx"), "utf8");
const documentSrc = readFileSync(join(root, "src/routes/library.$itemId.tsx"), "utf8");

function memoryStorage(): Storage {
  const data = new Map<string, string>();
  return {
    get length() {
      return data.size;
    },
    clear: () => data.clear(),
    getItem: (key) => data.get(key) ?? null,
    key: (index) => [...data.keys()][index] ?? null,
    removeItem: (key) => {
      data.delete(key);
    },
    setItem: (key, value) => {
      data.set(key, value);
    },
  };
}

const browsing: LibraryBrowseState = {
  view: { kind: "folder", folderId: "pac" },
  query: "clinic",
  fileTypeFilter: ".pdf",
  folderQuery: "alc",
  foldersExpanded: true,
  scrollY: 640,
};

test("opening a document and returning restores folder, search, filter, and scroll", () => {
  const storage = memoryStorage();
  writeLibraryBrowseState(storage, browsing);
  const restored = readLibraryBrowseState(storage);
  assert.deepEqual(restored, browsing);
  assert.deepEqual(libraryListQuery(restored), {
    q: "clinic",
    folder_id: "pac",
    extension: ".pdf",
  });
  assert.equal(
    libraryScrollToRestore(restored.scrollY, { contentReady: false, alreadyRestored: false }),
    null,
  );
  assert.equal(
    libraryScrollToRestore(restored.scrollY, { contentReady: true, alreadyRestored: false }),
    640,
  );
  assert.equal(
    libraryScrollToRestore(restored.scrollY, { contentReady: true, alreadyRestored: true }),
    null,
  );
  assert.match(librarySrc, /readLibraryBrowseState/);
  assert.match(librarySrc, /libraryListQuery/);
  assert.doesNotMatch(librarySrc, /sessionStorage\.removeItem|localStorage\.removeItem/);
  assert.doesNotMatch(documentSrc, /sessionStorage\.removeItem|EMPTY_LIBRARY_BROWSE/);
});

test("library search and folder context survive the document view unmounting", () => {
  const storage = memoryStorage();
  writeLibraryBrowseState(storage, {
    ...browsing,
    view: { kind: "favorites" },
    query: "notes",
    fileTypeFilter: "document",
  });
  const afterUnmount = readLibraryBrowseState(storage);
  assert.equal(afterUnmount.view.kind, "favorites");
  assert.equal(afterUnmount.query, "notes");
  assert.equal(afterUnmount.fileTypeFilter, "document");
  assert.deepEqual(libraryListQuery(afterUnmount), {
    q: "notes",
    item_type: "document",
    favorites: true,
  });
});

test("choosing another folder, search, or filter replaces the restored location", () => {
  const restored = parseLibraryBrowseState(browsing);
  const nextFolder = applyLibraryBrowseChange(restored, {
    view: { kind: "folder", folderId: "other" },
  });
  assert.deepEqual(nextFolder.view, { kind: "folder", folderId: "other" });
  assert.equal(nextFolder.query, "clinic");
  assert.equal(nextFolder.fileTypeFilter, ".pdf");
  assert.equal(nextFolder.folderQuery, "alc");
  assert.equal(nextFolder.scrollY, 0);
  assert.equal(libraryListQuery(nextFolder).folder_id, "other");

  const nextSearch = applyLibraryBrowseChange(nextFolder, { query: "rehab" });
  assert.equal(nextSearch.query, "rehab");
  assert.notEqual(nextSearch.query, restored.query);
  assert.equal(libraryListQuery(nextSearch).q, "rehab");

  const nextFilter = applyLibraryBrowseChange(nextSearch, { fileTypeFilter: ".docx" });
  assert.equal(nextFilter.fileTypeFilter, ".docx");
  assert.equal(libraryListQuery(nextFilter).extension, ".docx");

  const home = applyLibraryBrowseChange(nextFilter, { view: { kind: "home" } });
  assert.equal(home.view.kind, "home");
  assert.equal(home.scrollY, 0);
  assert.equal(libraryListQuery(home).homeLimit, 5);
  assert.equal(libraryListQuery(EMPTY_LIBRARY_BROWSE).recent, true);
});

test("corrupt library browse storage falls back to the initial view", () => {
  assert.deepEqual(parseLibraryBrowseState(null), EMPTY_LIBRARY_BROWSE);
  assert.deepEqual(parseLibraryBrowseState({ view: { kind: "nope" }, scrollY: -20 }), {
    ...EMPTY_LIBRARY_BROWSE,
  });
  const storage = memoryStorage();
  storage.setItem("multimind.library.browse", "{");
  assert.deepEqual(readLibraryBrowseState(storage), EMPTY_LIBRARY_BROWSE);
});
