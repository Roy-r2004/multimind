import assert from "node:assert/strict";
import test from "node:test";

import { libraryViewAfterFolderDelete } from "../../src/lib/libraryUi.ts";

test("deleting the active nested folder selects its parent", () => {
  assert.deepEqual(
    libraryViewAfterFolderDelete(
      { kind: "folder", folderId: "child" },
      { id: "child", parent_id: "parent" },
    ),
    { kind: "folder", folderId: "parent" },
  );
});

test("deleting the active root folder returns to all items", () => {
  assert.deepEqual(
    libraryViewAfterFolderDelete(
      { kind: "folder", folderId: "root" },
      { id: "root", parent_id: null },
    ),
    { kind: "all" },
  );
});

test("deleting an inactive folder preserves the current view", () => {
  const current = { kind: "folder" as const, folderId: "active" };
  assert.equal(libraryViewAfterFolderDelete(current, { id: "other", parent_id: null }), current);
});
