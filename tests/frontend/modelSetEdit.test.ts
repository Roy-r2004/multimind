import assert from "node:assert/strict";
import test from "node:test";

import {
  clonedSystemModelSetName,
  IN_PLACE_EDITABLE_SYSTEM_SLUG,
  shouldCloneSystemModelSet,
} from "../../src/lib/modelSetEdit.ts";

const SYSTEM_MODEL_SETS = new Set([
  "referee",
  "set-7edaefc8",
  "balanced",
  "coding",
  "business",
  "research",
]);

test("Chafic Ultimate is saved in place, not cloned", () => {
  assert.equal(IN_PLACE_EDITABLE_SYSTEM_SLUG, "set-7edaefc8");
  assert.equal(shouldCloneSystemModelSet("set-7edaefc8", SYSTEM_MODEL_SETS), false);
});

test("other protected system sets still clone on save", () => {
  for (const slug of ["referee", "balanced", "coding", "business", "research"]) {
    assert.equal(shouldCloneSystemModelSet(slug, SYSTEM_MODEL_SETS), true);
  }
  assert.equal(
    clonedSystemModelSetName("Chafiq Referee"),
    "My Chafiq Referee",
  );
  assert.equal(clonedSystemModelSetName("My already prefixed"), "My already prefixed");
});

test("user-created sets are never cloned by the system-set save path", () => {
  assert.equal(shouldCloneSystemModelSet("set-ab12cd34", SYSTEM_MODEL_SETS), false);
});
