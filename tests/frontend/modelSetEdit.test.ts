import assert from "node:assert/strict";
import test from "node:test";

import {
  clonedSystemModelSetName,
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

test("protected system sets, including Chafic Ultimate, clone on save", () => {
  for (const slug of ["referee", "set-7edaefc8", "balanced", "coding", "business", "research"]) {
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
