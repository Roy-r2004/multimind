import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_MODEL_SET_TITLE,
  findDefaultModelSetId,
  normalizeModelSetTitle,
  resolveModelSetIdFromTurns,
  selectExistingModelSetId,
} from "../../src/lib/modelSetSelection.ts";

function set(id: string, name: string) {
  return { id, name };
}

test("normalizeModelSetTitle trims, lowercases, and collapses whitespace", () => {
  assert.equal(
    normalizeModelSetTitle("  Chafic   Ultimate Model Set "),
    "chafic ultimate model set",
  );
});

test("fresh selection prefers exact title chafic ultimate model set", () => {
  const sets = [
    set("referee", "Chafiq Referee"),
    set("ultimate-1", "chafic ultimate model set"),
    set("balanced", "Balanced Set"),
  ];
  assert.equal(selectExistingModelSetId(sets, ""), "ultimate-1");
  assert.equal(findDefaultModelSetId(sets), "ultimate-1");
});

test("new chat / empty current id selects default for any org-visible list", () => {
  const orgA = [set("a", "Other"), set("u", DEFAULT_MODEL_SET_TITLE)];
  const orgB = [set("b", "Coding"), set("u2", "Chafic Ultimate Model Set")];
  assert.equal(selectExistingModelSetId(orgA, ""), "u");
  assert.equal(selectExistingModelSetId(orgB, ""), "u2");
});

test("manual current selection is not overwritten when still present", () => {
  const sets = [
    set("ultimate-1", "chafic ultimate model set"),
    set("coding", "Coding Set"),
  ];
  assert.equal(selectExistingModelSetId(sets, "coding"), "coding");
});

test("existing chat turns restore newest model_set_id", () => {
  const id = resolveModelSetIdFromTurns([
    { model_set_id: "old-set", created_at: "2026-01-01T00:00:00Z" },
    { model_set_id: "chat-set", created_at: "2026-06-01T00:00:00Z" },
    { model_set_id: "mid-set", created_at: "2026-03-01T00:00:00Z" },
  ]);
  assert.equal(id, "chat-set");
});

test("empty turns do not invent a chat model set", () => {
  assert.equal(resolveModelSetIdFromTurns([]), null);
});

test("missing target set falls back to legacy referee then first set", () => {
  const withReferee = [
    set("balanced", "Balanced Set"),
    set("referee", "Chafiq Referee"),
  ];
  assert.equal(selectExistingModelSetId(withReferee, ""), "referee");

  const nameOnly = [set("x", "My Referee Clone"), set("y", "Other")];
  assert.equal(selectExistingModelSetId(nameOnly, ""), "x");

  const plain = [set("first", "Alpha"), set("second", "Beta")];
  assert.equal(selectExistingModelSetId(plain, ""), "first");
});

test("no model sets returns empty string", () => {
  assert.equal(selectExistingModelSetId([], ""), "");
  assert.equal(findDefaultModelSetId([]), null);
});

test("organization scoping is list-based: only sets in the provided list can win", () => {
  const orgList = [set("org-only", "Org Set"), set("u", "chafic ultimate model set")];
  assert.equal(selectExistingModelSetId(orgList, "other-org-set"), "u");
  assert.equal(findDefaultModelSetId([set("org-only", "Org Set")]), null);
});

test("duplicate titles select deterministically by lowest id", () => {
  const sets = [
    set("z-set", "chafic ultimate model set"),
    set("a-set", "chafic ultimate model set"),
    set("m-set", "chafic ultimate model set"),
  ];
  assert.equal(findDefaultModelSetId(sets), "a-set");
  assert.equal(selectExistingModelSetId(sets, ""), "a-set");
});

test("shared/public chat helpers do not alter selection APIs (pure functions only)", () => {
  // Shared page never calls selectExistingModelSetId; verifying purity keeps that contract.
  const sets = [set("u", "chafic ultimate model set")];
  const first = selectExistingModelSetId(sets, "");
  const second = selectExistingModelSetId(sets, "");
  assert.equal(first, second);
  assert.equal(first, "u");
});
