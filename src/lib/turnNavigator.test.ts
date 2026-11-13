import { describe, expect, it } from "vitest";

import type { ApiTurn } from "@/lib/api/types";
import {
  buildTurnNavigatorItems,
  clampTurnNavigatorIndex,
  nextTurnNavigatorIndex,
  previousTurnNavigatorIndex,
  previewFromQuestionText,
  reconcileTurnNavigatorIndex,
  turnNavigatorIndexById,
  turnNavigatorPreview,
  TURN_NAVIGATOR_PREVIEW_MAX_LENGTH,
} from "@/lib/turnNavigator";

function turn(overrides: Partial<ApiTurn> = {}): ApiTurn {
  return {
    id: "t1",
    chat_id: "c1",
    user_message: "Hello",
    model_set_id: "set1",
    strategy: "balanced" as ApiTurn["strategy"],
    verdict_model: "m1",
    status: "complete",
    model_answers: [],
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

describe("previewFromQuestionText", () => {
  it("returns short text unchanged", () => {
    expect(previewFromQuestionText("What is the capital of France?")).toBe(
      "What is the capital of France?",
    );
  });

  it("normalizes newlines and extra whitespace", () => {
    expect(previewFromQuestionText("Line one\n\n  Line   two\n\tLine three")).toBe(
      "Line one Line two Line three",
    );
  });

  it("truncates with an ellipsis past the max length", () => {
    const long = "a".repeat(200);
    const preview = previewFromQuestionText(long);
    expect(preview.length).toBe(TURN_NAVIGATOR_PREVIEW_MAX_LENGTH + 1);
    expect(preview.endsWith("…")).toBe(true);
  });

  it("never scans more than a small prefix of a huge question", () => {
    // A few thousand lines; if this scanned the whole string the line-count
    // alone (not just byte count) would make a naive implementation slow.
    const huge = Array.from({ length: 5000 }, (_, i) => `line ${i} `.repeat(20)).join("\n");
    const start = performance.now();
    const preview = previewFromQuestionText(huge);
    const elapsed = performance.now() - start;
    expect(preview.endsWith("…")).toBe(true);
    expect(preview.length).toBeLessThanOrEqual(TURN_NAVIGATOR_PREVIEW_MAX_LENGTH + 1);
    expect(elapsed).toBeLessThan(50);
  });

  it("returns empty string for empty or whitespace-only text", () => {
    expect(previewFromQuestionText("")).toBe("");
    expect(previewFromQuestionText("   \n\t  ")).toBe("");
    expect(previewFromQuestionText(null)).toBe("");
    expect(previewFromQuestionText(undefined)).toBe("");
  });
});

describe("turnNavigatorPreview", () => {
  it("falls back to an attachment summary when there is no text", () => {
    expect(
      turnNavigatorPreview(turn({ user_message: "", attachments: [{ id: "a1" } as never] })),
    ).toBe("1 attachment");
    expect(
      turnNavigatorPreview(
        turn({ user_message: "  ", attachments: [{ id: "a1" } as never, { id: "a2" } as never] }),
      ),
    ).toBe("2 attachments");
  });

  it("falls back to a generic label when there is neither text nor attachments", () => {
    expect(turnNavigatorPreview(turn({ user_message: "" }))).toBe("Untitled question");
  });

  it("prefers question text over attachments when both exist", () => {
    expect(
      turnNavigatorPreview(turn({ user_message: "Real question", attachments: [{ id: "a1" } as never] })),
    ).toBe("Real question");
  });
});

describe("buildTurnNavigatorItems", () => {
  it("builds one item per turn, preserving order", () => {
    const turns = [turn({ id: "t1" }), turn({ id: "t2" }), turn({ id: "t3" })];
    const items = buildTurnNavigatorItems(turns);
    expect(items.map((i) => i.turnId)).toEqual(["t1", "t2", "t3"]);
    expect(items.map((i) => i.index)).toEqual([0, 1, 2]);
  });

  it("returns an empty list for an empty conversation", () => {
    expect(buildTurnNavigatorItems([])).toEqual([]);
  });

  it("handles a single-question conversation", () => {
    const items = buildTurnNavigatorItems([turn({ id: "only" })]);
    expect(items).toHaveLength(1);
    expect(items[0].turnId).toBe("only");
  });
});

describe("clampTurnNavigatorIndex", () => {
  it("clamps within bounds", () => {
    expect(clampTurnNavigatorIndex(-5, 3)).toBe(0);
    expect(clampTurnNavigatorIndex(1, 3)).toBe(1);
    expect(clampTurnNavigatorIndex(10, 3)).toBe(2);
  });

  it("returns -1 for an empty list", () => {
    expect(clampTurnNavigatorIndex(0, 0)).toBe(-1);
  });
});

describe("next/previousTurnNavigatorIndex", () => {
  it("advances and retreats within bounds", () => {
    expect(nextTurnNavigatorIndex(0, 3)).toBe(1);
    expect(previousTurnNavigatorIndex(1, 3)).toBe(0);
  });

  it("stays put at the boundaries (disabled-arrow behavior)", () => {
    expect(nextTurnNavigatorIndex(2, 3)).toBe(2);
    expect(previousTurnNavigatorIndex(0, 3)).toBe(0);
  });

  it("handles an empty list", () => {
    expect(nextTurnNavigatorIndex(0, 0)).toBe(-1);
    expect(previousTurnNavigatorIndex(0, 0)).toBe(-1);
  });
});

describe("turnNavigatorIndexById", () => {
  const items = buildTurnNavigatorItems([turn({ id: "a" }), turn({ id: "b" })]);

  it("finds the matching index", () => {
    expect(turnNavigatorIndexById(items, "b")).toBe(1);
  });

  it("returns -1 when not found or id is null", () => {
    expect(turnNavigatorIndexById(items, "missing")).toBe(-1);
    expect(turnNavigatorIndexById(items, null)).toBe(-1);
  });
});

describe("reconcileTurnNavigatorIndex", () => {
  it("follows the same turn id after a new question is appended", () => {
    const before = buildTurnNavigatorItems([turn({ id: "a" }), turn({ id: "b" })]);
    const after = buildTurnNavigatorItems([turn({ id: "a" }), turn({ id: "b" }), turn({ id: "c" })]);
    expect(reconcileTurnNavigatorIndex("b", 1, after)).toBe(1);
  });

  it("falls back to a clamped position after the active turn is deleted", () => {
    const after = buildTurnNavigatorItems([turn({ id: "a" }), turn({ id: "c" })]);
    // Previously on index 1 ("b", now deleted) in a 3-item list.
    expect(reconcileTurnNavigatorIndex("b", 1, after)).toBe(1);
  });

  it("clamps to the last item when the active turn and trailing turns are all deleted", () => {
    const after = buildTurnNavigatorItems([turn({ id: "a" })]);
    expect(reconcileTurnNavigatorIndex("b", 2, after)).toBe(0);
  });

  it("returns -1 when the conversation becomes empty", () => {
    expect(reconcileTurnNavigatorIndex("a", 0, [])).toBe(-1);
  });

  it("re-points to a turn's new index after it moves (e.g. a sort after regenerate)", () => {
    const after = buildTurnNavigatorItems([turn({ id: "c" }), turn({ id: "a" })]);
    expect(reconcileTurnNavigatorIndex("a", 1, after)).toBe(1);
  });
});
