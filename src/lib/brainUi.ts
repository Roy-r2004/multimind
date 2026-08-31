import type { ApiBrain } from "@/lib/api/types";

export const EMPTY_BRAIN_PROFILE_DESCRIPTION =
  "Your Brain profile will develop as you use MultiMind.";

export const COGNITIVE_BARS = [
  { label: "Reasoning Depth", value: 96 },
  { label: "Pattern Recognition", value: 92 },
  { label: "Strategic Foresight", value: 94 },
  { label: "Structured Thinking", value: 91 },
  { label: "Adaptability", value: 88 },
];

export function parseBrainStyleTags(style: string): string[] {
  if (!style?.trim()) return [];
  const parts = style
    .split(/[,;·|/]/)
    .map((value) => value.trim())
    .filter((value) => value.length > 2 && value.length < 40);
  return parts.length >= 2 ? parts.slice(0, 6) : [];
}

function firstMeaningful(items: string[]): string | undefined {
  return items.map((item) => item.trim()).find(Boolean);
}

export function brainProfilePresentation(brain: ApiBrain) {
  const summary = brain.summary.trim();
  const thinkingStyle = brain.thinking_style.trim();
  const styleTags = parseBrainStyleTags(thinkingStyle);
  return {
    description: summary || thinkingStyle || EMPTY_BRAIN_PROFILE_DESCRIPTION,
    styleTags,
    positiveChip: firstMeaningful(brain.likes),
    negativeChip: firstMeaningful(brain.dislikes),
    styleChip: styleTags[0],
    knowledgeCount: brain.knowledge_count ?? brain.knowledge_items?.length ?? 0,
  };
}
