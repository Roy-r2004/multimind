import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { CircleDollarSign, Loader2, Sparkles } from "lucide-react";
import { GlassCard, PageHeader } from "@/components/cinematic/PageChrome";
import { api } from "@/lib/api";
import type { ApiCostSummary } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";
import { useModels } from "@/lib/models";
import { formatCost, formatTokens } from "@/lib/cost";

export const Route = createFileRoute("/costs")({
  head: () => ({ meta: [{ title: "Costs — MultiAI" }] }),
  component: CostsPage,
});

function CostsPage() {
  const { authHeaders, isAuthenticated } = useAuth();
  const { modelById } = useModels();
  const [summary, setSummary] = useState<ApiCostSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const auth = authHeaders();
    if (!auth) {
      setLoading(false);
      return;
    }
    void api.costs
      .summary(auth)
      .then(setSummary)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load costs"))
      .finally(() => setLoading(false));
  }, [authHeaders]);

  const byModel = useMemo(() => {
    if (!summary) return [];
    const total = summary.by_model.reduce((s, m) => s + m.cost_usd, 0) || 1;
    return summary.by_model.map((m) => ({
      id: m.model_id,
      label: modelById(m.model_id).name,
      cost: m.cost_usd,
      tokens: m.tokens,
      pct: Math.round((m.cost_usd / total) * 100),
      color: modelById(m.model_id).color,
    }));
  }, [summary, modelById]);

  const conicGradient = useMemo(() => {
    if (byModel.length === 0) return "conic-gradient(#94a3b8 0 100%)";
    let acc = 0;
    const stops = byModel.map((m) => {
      const start = acc;
      acc += m.pct;
      return `${m.color} ${start}% ${acc}%`;
    });
    return `conic-gradient(${stops.join(", ")})`;
  }, [byModel]);

  if (!isAuthenticated) {
    return (
      <AppShell>
        <div className="mx-auto max-w-lg px-6 py-20 text-center text-sm text-muted-foreground">
          Log in to view usage and costs.
        </div>
      </AppShell>
    );
  }

  if (loading) {
    return (
      <AppShell>
        <div className="flex justify-center py-20">
          <Loader2 className="size-6 animate-spin text-muted-foreground" />
        </div>
      </AppShell>
    );
  }

  if (error || !summary) {
    return (
      <AppShell>
        <div className="mx-auto max-w-lg px-6 py-20 text-center text-sm text-destructive">
          {error ?? "No cost data"}
        </div>
      </AppShell>
    );
  }

  const remaining = Math.max(0, summary.budget_usd - summary.month_usd);
  const hasUsage = summary.month_usd > 0 || summary.by_model.length > 0;

  return (
    <AppShell>
      <div className="mx-auto max-w-6xl px-6 py-10">
        <PageHeader
          eyebrow="Usage"
          title="Costs"
          description="Actual API spend recorded from your chats — answers, verdicts, and decision insurance. Each charge uses OpenRouter's reported cost per request."
        />

        <div className="mt-8 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <StatCard
            title="Monthly Budget"
            amount={`${formatCost(summary.month_usd)} / ${formatCost(summary.budget_usd)}`}
            body={`${Math.round(summary.budget_used_pct)}% used`}
            footer={`${formatCost(remaining)} remaining`}
            progress={summary.budget_used_pct}
          />
          <StatCard title="Today" amount={formatCost(summary.today_usd)} body="Recorded spend today" />
          <StatCard title="This Week" amount={formatCost(summary.week_usd)} body="Last 7 days" />
          <StatCard
            title="This Month"
            amount={formatCost(summary.month_usd)}
            body={`${formatTokens(summary.month_tokens)} tokens`}
          />
        </div>

        {!hasUsage ? (
          <GlassCard className="mt-8 p-12 text-center">
            <CircleDollarSign className="mx-auto size-8 text-muted-foreground" />
            <p className="mt-3 text-sm text-muted-foreground">
              No usage recorded yet. Send a message in chat — costs appear here after each turn completes.
            </p>
          </GlassCard>
        ) : (
          <div className="mt-8 grid gap-4 lg:grid-cols-[1.1fr_0.9fr]">
            <GlassCard className="p-6 sm:p-7">
              <h2 className="text-base font-semibold tracking-tight">Top Models by Spend</h2>
              <p className="mt-1 text-sm text-muted-foreground">Actual recorded cost this month</p>

              <div className="mt-8 flex flex-col gap-6 lg:flex-row lg:items-center lg:gap-8">
                <div className="mx-auto flex size-64 items-center justify-center">
                  <div
                    className="relative flex size-56 items-center justify-center rounded-full"
                    style={{ background: conicGradient }}
                  >
                    <div className="absolute inset-4 rounded-full bg-background" />
                    <div className="relative text-center">
                      <div className="text-[10px] font-semibold uppercase tracking-[0.24em] text-muted-foreground">
                        Total
                      </div>
                      <div className="mt-1 text-2xl font-semibold tracking-tight text-foreground">
                        {formatCost(summary.month_usd)}
                      </div>
                    </div>
                  </div>
                </div>

                <div className="w-full max-w-sm">
                  <div className="grid grid-cols-[1fr_auto_auto] gap-x-4 gap-y-2 text-sm text-muted-foreground">
                    {byModel.map((item) => (
                      <div key={item.id} className="contents">
                        <div className="flex items-center gap-2">
                          <span
                            className="size-2.5 rounded-full"
                            style={{ backgroundColor: item.color }}
                          />
                          <span className="text-foreground">{item.label}</span>
                        </div>
                        <span className="text-right font-medium text-foreground">
                          {formatCost(item.cost)}
                        </span>
                        <span className="text-right">{item.pct}%</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </GlassCard>

            <GlassCard className="p-6 sm:p-7">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <h2 className="text-base font-semibold tracking-tight">Usage Breakdown</h2>
                  <p className="mt-1 text-sm text-muted-foreground">Current billing period</p>
                </div>
                <div className="rounded-full bg-accent p-2 text-muted-foreground">
                  <CircleDollarSign className="size-4" />
                </div>
              </div>

              <div className="mt-8 space-y-2.5">
                {[
                  ["Total Tokens", formatTokens(summary.month_tokens)],
                  ["Total Spend", formatCost(summary.month_usd)],
                  ["Top Model", byModel[0]?.label ?? "—"],
                  ["Budget Used", `${Math.round(summary.budget_used_pct)}%`],
                ].map(([label, value]) => (
                  <div
                    key={label}
                    className="flex items-center justify-between rounded-xl border border-white/10 bg-background/70 px-4 py-3.5"
                  >
                    <span className="text-sm text-muted-foreground">{label}</span>
                    <span className="text-sm font-semibold text-foreground">{value}</span>
                  </div>
                ))}
              </div>
            </GlassCard>
          </div>
        )}
      </div>
    </AppShell>
  );
}

function StatCard({
  title,
  amount,
  body,
  footer,
  progress,
}: {
  title: string;
  amount: string;
  body: string;
  footer?: string;
  progress?: number;
}) {
  return (
    <GlassCard className="p-5 sm:p-6">
      <div className="flex items-center gap-2 text-sm font-medium text-foreground">
        <Sparkles className="size-4 text-primary" /> {title}
      </div>
      <div className="mt-5 text-3xl font-semibold tracking-tight text-foreground">{amount}</div>
      <div className="mt-2 text-sm text-muted-foreground">{body}</div>
      {progress !== undefined ? (
        <div className="mt-5">
          <div className="h-2 rounded-full bg-muted">
            <div
              className="h-2 rounded-full bg-primary"
              style={{ width: `${Math.min(100, progress)}%` }}
            />
          </div>
          {footer && <div className="mt-2 text-sm text-muted-foreground">{footer}</div>}
        </div>
      ) : null}
    </GlassCard>
  );
}
