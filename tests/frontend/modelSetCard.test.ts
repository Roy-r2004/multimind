import assert from "node:assert/strict";
import test from "node:test";

import { modelSetCardState } from "../../src/lib/modelSetCard.ts";
import { selectExistingModelSetId } from "../../src/lib/modelSetSelection.ts";

const sets = [
  { id: "coding", name: "Coding Set" },
  { id: "ultimate-1", name: "chafic ultimate model set" },
  { id: "balanced", name: "Balanced Set" },
];

test("only the store's active id is marked selected", () => {
  const selected = modelSetCardState("coding", "coding");
  const other = modelSetCardState("ultimate-1", "coding");

  assert.equal(selected.active, true);
  assert.equal(selected.badge, "Active set");
  assert.match(selected.className, /border-primary/);
  assert.match(selected.className, /bg-primary\/5/);
  assert.match(selected.className, /shadow-/);

  assert.equal(other.active, false);
  assert.equal(other.badge, null);
  assert.match(other.className, /border-border/);
  assert.doesNotMatch(other.className, /bg-primary\/5/);
});

test("hover and focus classes stay on both states and do not replace the active fill", () => {
  const selected = modelSetCardState("coding", "coding");
  const idle = modelSetCardState("balanced", "coding");

  assert.match(selected.className, /hover:bg-primary\/10/);
  assert.match(selected.className, /focus-visible:ring-2/);
  assert.match(idle.className, /hover:border-primary\/30/);
  assert.match(idle.className, /focus-visible:ring-2/);
  assert.doesNotMatch(idle.className, /border-primary(?!\/)/);
});

test("switching the active id moves the badge", () => {
  let activeId = "ultimate-1";
  assert.equal(modelSetCardState("ultimate-1", activeId).active, true);
  assert.equal(modelSetCardState("balanced", activeId).active, false);

  activeId = "balanced";
  assert.equal(modelSetCardState("ultimate-1", activeId).badge, null);
  assert.equal(modelSetCardState("balanced", activeId).badge, "Active set");
  assert.equal(modelSetCardState("coding", activeId).active, false);
});

test("refresh keeps the previously selected set instead of a title default", () => {
  const restored = selectExistingModelSetId(sets, "balanced");
  assert.equal(modelSetCardState("balanced", restored).active, true);
  assert.equal(modelSetCardState("ultimate-1", restored).active, false);
});

test("an unresolved empty selection does not mark any card active", () => {
  for (const set of sets) {
    assert.equal(modelSetCardState(set.id, "").active, false);
  }
});
