import assert from "node:assert/strict";
import test from "node:test";
import { findSelectionOffsets } from "../../src/lib/verdictSelectionNavigation.ts";
import { pinnedVerdictLabel } from "../../src/lib/pinnedVerdicts.ts";
import { mapApiChat } from "../../src/lib/chatHistory.ts";

test("selection labels normalize whitespace and remain compact", () => {
  const pin = { verdictId: "v", turnId: "t", pinType: "selection" as const };
  assert.equal(
    pinnedVerdictLabel({ ...pin, selectedText: "  Perturbation\n  Theory\t basics " }, []),
    "Perturbation Theory basics",
  );
  const label = pinnedVerdictLabel({ ...pin, selectedText: "sentence ".repeat(30) }, []);
  assert.equal(label.length, 64);
  assert.ok(label.endsWith("…"));
  assert.equal(pinnedVerdictLabel({ ...pin, selectedText: " " }, []), "Pinned selection");
});

test("legacy text matching is scoped, whitespace tolerant, and rejects ambiguity", () => {
  assert.deepEqual(findSelectionOffsets("Intro. A small sentence. End.", "A small sentence."), {
    start: 7,
    end: 24,
  });
  assert.deepEqual(findSelectionOffsets("one\n  two", "one two"), { start: 0, end: 9 });
  assert.equal(findSelectionOffsets("repeated repeated", "repeated"), null);
  assert.equal(findSelectionOffsets("source verdict", "another verdict"), null);
  assert.equal(findSelectionOffsets("text", "  "), null);
});

test("persisted offsets distinguish identical text and survive API/history mapping", () => {
  const text = "same words; same words";
  const locators = [
    { start: 0, end: 10, quote: "same words" },
    { start: 12, end: 22, quote: "same words" },
  ];
  const api = JSON.parse(
    JSON.stringify({
      id: "c",
      title: "Chat",
      updated_at: new Date().toISOString(),
      pinned_verdicts: locators.map((locator, index) => ({
        id: `pin-${index}`,
        verdict_id: "v",
        turn_id: "t",
        pin_type: "selection",
        selected_text: "same words",
        selection_locator: locator,
      })),
    }),
  );
  const pins = mapApiChat(api).pinnedVerdicts;
  assert.deepEqual(
    pins.map((pin) => findSelectionOffsets(text, pin.selectedText!, pin.selectionLocator)),
    [
      { start: 0, end: 10 },
      { start: 12, end: 22 },
    ],
  );
});

test("stale offsets verify the quote and only fall back to a unique match", () => {
  const locator = { start: 0, end: 6, quote: "target" };
  assert.deepEqual(findSelectionOffsets("prefix target", "target", locator), { start: 7, end: 13 });
  assert.equal(findSelectionOffsets("prefix target target", "target", locator), null);
  assert.equal(findSelectionOffsets("removed fragment", "target", locator), null);
});
