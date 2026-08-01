// Token usage + cost helpers — costs always come from the API (OpenRouter reported).

export type TokenUsage = {
  input: number;
  output: number;
  total: number;
};

export type UsageKind = "answer" | "verdict" | "insurance" | "lesson" | "brain" | "embedding" | "scraping" | "helper" | "other";

export type UsageBreakdown = {
  modelId: string;
  modelName: string;
  kind: UsageKind;
  usage: TokenUsage;
  cost: number;
};

export type CostRecord = {
  id: string;
  chatId: string;
  projectId: string | null;
  modelId: string;
  kind: UsageKind;
  usage: TokenUsage;
  cost: number;
  at: number;
};

export function estimateTokens(text: string): number {
  return Math.max(1, Math.round(text.length / 4));
}

export function makeUsage(input: number, output: number): TokenUsage {
  return { input, output, total: input + output };
}

export function breakdownFromApi(
  modelId: string,
  kind: UsageKind,
  usage: TokenUsage,
  costUsd: number,
  modelName?: string,
): UsageBreakdown {
  return {
    modelId,
    modelName: modelName ?? modelId,
    kind,
    usage,
    cost: costUsd,
  };
}

export function formatTokens(n: number): string {
  if (n < 1000) return `${n}`;
  return `${(n / 1000).toFixed(1)}K`;
}

export function formatTokensExact(n: number): string {
  return n.toLocaleString("en-US");
}

export function formatTokensLabel(n: number): string {
  return `${formatTokensExact(n)} tokens`;
}

export function formatCost(n: number): string {
  if (!Number.isFinite(n) || n === 0) return "$0.00";
  if (n >= 0.01) return `$${n.toFixed(2)}`;
  if (n >= 0.0001) return `$${n.toFixed(4)}`;
  return `$${n.toFixed(6)}`;
}

export function formatCompactDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

/** Friendly activity label for usage rows (never show raw operation codes as primary). */
export function friendlyUsageActivity(operation?: string | null, kind?: string | null): string {
  const op = (operation || "").trim().toLowerCase();
  const k = (kind || "").trim().toLowerCase();

  if (k === "embedding" || op.includes("embed")) return "Embedding";
  if (
    op === "council_answer" ||
    (op.includes("council") && !op.includes("scrap")) ||
    k === "answer"
  ) {
    return "Chat answer";
  }
  if (op === "verdict" || k === "verdict") return "Verdict";
  if (k === "brain" || (op.includes("brain") && !op.includes("embed"))) return "Brain";
  if (op.includes("lesson") || k === "lesson") return "Lesson";
  if (k === "blueprint" || op.includes("blueprint")) return "Blueprint";
  if (k === "planner" || op.includes("planner") || op.includes("team_plan")) return "Planner";
  if (
    k === "classification" ||
    op.includes("classif") ||
    op.includes("maps_classify") ||
    op.includes("maps_website") ||
    op.includes("maps_grid")
  ) {
    return "Classification";
  }
  if (k === "document" || op.includes("document")) return "Document";
  if (
    op.includes("scrap") ||
    op.includes("extract") ||
    op.includes("facility") ||
    op.includes("discovery") ||
    op.includes("official_source") ||
    k === "scraping" ||
    k === "extraction"
  ) {
    return "Scraping";
  }
  if (op.includes("prompt") || op.includes("helper") || k === "helper") return "AI helper";
  if (k === "insurance") return "Insurance";
  return "Other";
}

/** Short display name from catalog id / OpenRouter slug. */
export function friendlyModelLabel(modelId: string): string {
  const raw = (modelId || "").trim();
  if (!raw) return "Unknown model";
  let s = raw;
  if (s.startsWith("or:")) s = s.slice(3);
  s = s.replace(/--/g, "/");
  const parts = s.split("/");
  const leaf = parts[parts.length - 1] || s;
  return leaf
    .replace(/[-_]/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .replace(/\bGpt\b/gi, "GPT")
    .replace(/\bAi\b/g, "AI");
}

export type GroupTotals = { tokens: number; cost: number; count: number };

function groupBy(
  records: CostRecord[],
  key: (r: CostRecord) => string | null,
): Record<string, GroupTotals> {
  const out: Record<string, GroupTotals> = {};
  for (const r of records) {
    const k = key(r);
    if (k == null) continue;
    const g = out[k] ?? { tokens: 0, cost: 0, count: 0 };
    g.tokens += r.usage.total;
    g.cost += r.cost;
    g.count += 1;
    out[k] = g;
  }
  return out;
}

export function aggregateByModel(records: CostRecord[]): Record<string, GroupTotals> {
  return groupBy(records, (r) => r.modelId);
}

export function sumCost(records: CostRecord[]): number {
  return records.reduce((acc, r) => acc + r.cost, 0);
}
