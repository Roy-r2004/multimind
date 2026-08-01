// Token usage + cost helpers — costs always come from the API (OpenRouter reported).

export type TokenUsage = {
  input: number;
  output: number;
  total: number;
};

export type UsageKind = "answer" | "verdict" | "insurance";

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

export function formatCost(n: number): string {
  if (n >= 0.01) return `$${n.toFixed(2)}`;
  return `$${n.toFixed(4)}`;
}

/** Exact USD amount for a positive OpenRouter cost; null when unknown. */
export function formatCostAmount(cost: number | null | undefined): string | null {
  const body = formatPositiveCostBody(cost);
  return body ? `$${body}` : null;
}

/** @deprecated Prefer formatCostAmount for pill UI; kept for callers needing a labeled string. */
export function formatCallCostLabel(cost: number | null | undefined): string | null {
  return formatCostAmount(cost);
}

/** Labeled turn-total string; null when no valid positive costs. */
export function formatTurnTotalCostLabel(total: number | null | undefined): string | null {
  const amount = formatCostAmount(total);
  return amount ? `Turn total  ${amount}` : null;
}

/**
 * Sum valid positive answer costs + Verdict cost for one turn.
 * Uses fixed-point micros to avoid float display drift.
 */
export function calculateTurnCost(
  answers: Array<{ cost_usd?: number | null } | null | undefined>,
  verdictCost?: number | null,
): number | null {
  let micros = 0;
  let counted = false;
  for (const answer of answers) {
    const part = toCostMicros(answer?.cost_usd);
    if (part == null) continue;
    micros += part;
    counted = true;
  }
  const verdictPart = toCostMicros(verdictCost);
  if (verdictPart != null) {
    micros += verdictPart;
    counted = true;
  }
  if (!counted || micros <= 0) return null;
  return micros / COST_MICROS_SCALE;
}

const COST_MICROS_SCALE = 100_000_000;

function toCostMicros(cost: number | null | undefined): number | null {
  if (cost == null || !Number.isFinite(cost) || cost <= 0) return null;
  return Math.round(cost * COST_MICROS_SCALE);
}

function formatPositiveCostBody(cost: number | null | undefined): string | null {
  if (cost == null || !Number.isFinite(cost) || cost <= 0) return null;
  const body = trimTrailingZeros(cost.toFixed(8));
  if (!body || body === "0") return null;
  return body;
}

function trimTrailingZeros(value: string): string {
  if (!value.includes(".")) return value;
  return value.replace(/0+$/, "").replace(/\.$/, "");
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
