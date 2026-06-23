import { MODELS, modelById } from "@/lib/mock";

// ---------------------------------------------------------------------------
// Token usage + cost model
//
// This module is the single source of truth for usage/cost math so that the
// chat UI today and future analytics dashboards (cost per project, per chat,
// per model, per day/week/month) all share the same primitives.
// ---------------------------------------------------------------------------

export type TokenUsage = {
  input: number;
  output: number;
  total: number;
};

export type UsageKind = "answer" | "verdict";

/** Per-model breakdown shown in a single chat turn. */
export type UsageBreakdown = {
  modelId: string;
  modelName: string;
  kind: UsageKind;
  usage: TokenUsage;
  cost: number;
};

/**
 * Flat, append-only usage record. The chat UI derives these per turn, but the
 * same shape is what a future analytics layer would persist and aggregate.
 */
export type CostRecord = {
  id: string;
  chatId: string;
  projectId: string | null;
  modelId: string;
  kind: UsageKind;
  usage: TokenUsage;
  cost: number;
  /** epoch ms */
  at: number;
};

/** USD price per 1K tokens, split by input/output. */
export type ModelPrice = { input: number; output: number };

export const MODEL_PRICING: Record<string, ModelPrice> = {
  "gpt-4.1": { input: 0.003, output: 0.006 },
  claude: { input: 0.0025, output: 0.005 },
  gemini: { input: 0.0012, output: 0.0024 },
  mistral: { input: 0.0008, output: 0.0016 },
  deepseek: { input: 0.0006, output: 0.0012 },
  llama: { input: 0.0005, output: 0.001 },
  perplex: { input: 0.001, output: 0.002 },
};

const FALLBACK_PRICE: ModelPrice = { input: 0.002, output: 0.004 };

export function priceFor(modelId: string): ModelPrice {
  return MODEL_PRICING[modelId] ?? FALLBACK_PRICE;
}

/** Rough token estimate from text (~4 chars per token). */
export function estimateTokens(text: string): number {
  return Math.max(1, Math.round(text.length / 4));
}

export function makeUsage(input: number, output: number): TokenUsage {
  return { input, output, total: input + output };
}

export function costForUsage(modelId: string, usage: TokenUsage): number {
  const p = priceFor(modelId);
  return (usage.input / 1000) * p.input + (usage.output / 1000) * p.output;
}

export function breakdown(modelId: string, kind: UsageKind, usage: TokenUsage): UsageBreakdown {
  return {
    modelId,
    modelName: modelById(modelId).name,
    kind,
    usage,
    cost: costForUsage(modelId, usage),
  };
}

// ---------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------

/** Compact token count, e.g. 1200 -> "1.2K". */
export function formatTokens(n: number): string {
  if (n < 1000) return `${n}`;
  return `${(n / 1000).toFixed(1)}K`;
}

/** Exact token count with thousands separators, e.g. "1,200". */
export function formatTokensExact(n: number): string {
  return n.toLocaleString("en-US");
}

/** USD cost, e.g. 0.0042 -> "$0.0042". */
export function formatCost(n: number): string {
  return `$${n.toFixed(4)}`;
}

// ---------------------------------------------------------------------------
// Aggregation primitives — the foundation future dashboards build on.
// All pure functions over CostRecord[] so they can run on any data source.
// ---------------------------------------------------------------------------

export function sumUsage(records: CostRecord[]): TokenUsage {
  return records.reduce<TokenUsage>(
    (acc, r) => makeUsage(acc.input + r.usage.input, acc.output + r.usage.output),
    makeUsage(0, 0),
  );
}

export function sumCost(records: CostRecord[]): number {
  return records.reduce((acc, r) => acc + r.cost, 0);
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

export function aggregateByProject(records: CostRecord[]): Record<string, GroupTotals> {
  return groupBy(records, (r) => r.projectId);
}

export function aggregateByChat(records: CostRecord[]): Record<string, GroupTotals> {
  return groupBy(records, (r) => r.chatId);
}

export function filterByDateRange(
  records: CostRecord[],
  fromInclusive: number,
  toExclusive: number,
): CostRecord[] {
  return records.filter((r) => r.at >= fromInclusive && r.at < toExclusive);
}

function topGroup(totals: Record<string, GroupTotals>, by: keyof GroupTotals) {
  let bestKey: string | null = null;
  let best = -Infinity;
  for (const [k, g] of Object.entries(totals)) {
    if (g[by] > best) {
      best = g[by];
      bestKey = k;
    }
  }
  return bestKey ? { key: bestKey, totals: totals[bestKey] } : null;
}

export function mostExpensiveModel(records: CostRecord[]) {
  return topGroup(aggregateByModel(records), "cost");
}

export function mostUsedModel(records: CostRecord[]) {
  return topGroup(aggregateByModel(records), "tokens");
}

/** Convenience list of every known model id (handy for dashboard scaffolding). */
export const KNOWN_MODEL_IDS = MODELS.map((m) => m.id);
