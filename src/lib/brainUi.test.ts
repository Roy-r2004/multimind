import { describe, expect, it } from "vitest";

import type { ApiBrain } from "@/lib/api/types";
import {
  brainProfilePresentation,
  COGNITIVE_BARS,
  EMPTY_BRAIN_PROFILE_DESCRIPTION,
  parseBrainStyleTags,
} from "@/lib/brainUi";

function brain(overrides: Partial<ApiBrain> = {}): ApiBrain {
  return {
    user_name: "Real User",
    summary: "",
    thinking_style: "",
    likes: [],
    dislikes: [],
    memories: [],
    lesson_count: 0,
    ...overrides,
  };
}

describe("Brain UI presentation", () => {
  it("uses persisted summary before persisted thinking style", () => {
    const result = brainProfilePresentation(
      brain({ summary: "Persisted summary", thinking_style: "Analytical, Deliberate" }),
    );
    expect(result.description).toBe("Persisted summary");
    expect(result.styleTags).toEqual(["Analytical", "Deliberate"]);
  });

  it("uses persisted thinking style when the summary is empty", () => {
    expect(
      brainProfilePresentation(brain({ thinking_style: "Analytical, Deliberate" })).description,
    ).toBe("Analytical, Deliberate");
  });

  it("uses a neutral empty state without fabricated profile values", () => {
    const result = brainProfilePresentation(brain());
    const renderedValues = [result.description, ...result.styleTags].join(" ");
    expect(result.description).toBe(EMPTY_BRAIN_PROFILE_DESCRIPTION);
    expect(result.styleTags).toEqual([]);
    expect(result.positiveChip).toBeUndefined();
    expect(result.negativeChip).toBeUndefined();
    expect(result.styleChip).toBeUndefined();
    for (const fakeValue of [
      "Systems thinker. Builder. Clarity over noise.",
      "Personal memory. Structured intelligence.",
      "I don't collect information. I refine what's useful.",
      "First Principles",
      "Long-term",
      "Framework Driven",
      "Evidence-seeking",
    ]) {
      expect(renderedValues).not.toContain(fakeValue);
    }
  });

  it("builds visualization chips only from persisted values", () => {
    const result = brainProfilePresentation(
      brain({
        likes: ["", "Concrete evidence"],
        dislikes: ["Vague advice"],
        thinking_style: "Analytical, Long horizon",
      }),
    );
    expect(result.positiveChip).toBe("Concrete evidence");
    expect(result.negativeChip).toBe("Vague advice");
    expect(result.styleChip).toBe("Analytical");
  });

  it("omits a style chip when persisted prose cannot safely become tags", () => {
    expect(parseBrainStyleTags("A single long persisted description")).toEqual([]);
  });

  it("uses the API knowledge count and retains the item-count compatibility fallback", () => {
    expect(brainProfilePresentation(brain({ knowledge_count: 42 })).knowledgeCount).toBe(42);
    expect(
      brainProfilePresentation(
        brain({
          knowledge_items: [
            {
              id: "knowledge-1",
              source_type: "lesson",
              source_id: "lesson-1",
              title: "A lesson",
              content: "Persisted content",
            },
          ],
        }),
      ).knowledgeCount,
    ).toBe(1);
  });

  it("keeps the cognitive profile scores unchanged", () => {
    expect(COGNITIVE_BARS).toEqual([
      { label: "Reasoning Depth", value: 96 },
      { label: "Pattern Recognition", value: 92 },
      { label: "Strategic Foresight", value: 94 },
      { label: "Structured Thinking", value: 91 },
      { label: "Adaptability", value: 88 },
    ]);
  });
});
