import { createFileRoute, Link } from "@tanstack/react-router";
import { useCallback, useEffect, useState } from "react";
import { Info, RefreshCw } from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { GlassCard, PageHeader } from "@/components/cinematic/PageChrome";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import type { ApiUserUsageRecord, ApiUserUsageSummary } from "@/lib/api/types";
import {
  formatCompactDate,
  formatCost,
  formatTokensExact,
  formatTokensLabel,
  friendlyModelLabel,
  friendlyUsageActivity,
} from "@/lib/cost";

export const Route = createFileRoute("/usage")({
  head: () => ({ meta: [{ title: "Usage & Costs — MultiAI" }] }),
  component: UsagePage,
});

const PAGE_SIZE = 20;

function UsagePage() {
  const { session, authHeaders } = useAuth();
  const [summary, setSummary] = useState<ApiUserUsageSummary | null>(null);
  const [records, setRecords] = useState<ApiUserUsageRecord[]>([]);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadSummaryAndFirstPage = useCallback(async () => {
    const auth = authHeaders();
    if (!auth) {
      setError("Sign in to view your usage.");
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const [summaryRes, recordsRes] = await Promise.all([
        api.usage.summary(auth),
        api.usage.records(auth, { page: 1, limit: PAGE_SIZE }),
      ]);
      setSummary(summaryRes);
      setRecords(recordsRes.items);
      setTotal(recordsRes.total);
      setPage(1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load usage");
    } finally {
      setLoading(false);
    }
  }, [authHeaders]);

  useEffect(() => {
    void loadSummaryAndFirstPage();
  }, [loadSummaryAndFirstPage]);

  async function loadMore() {
    const auth = authHeaders();
    if (!auth || loadingMore) return;
    const next = page + 1;
    setLoadingMore(true);
    try {
      const recordsRes = await api.usage.records(auth, { page: next, limit: PAGE_SIZE });
      setRecords((prev) => [...prev, ...recordsRes.items]);
      setTotal(recordsRes.total);
      setPage(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load more");
    } finally {
      setLoadingMore(false);
    }
  }

  const hasMore = records.length < total;

  if (!session) {
    return (
      <AppShell>
        <div className="mx-auto max-w-3xl px-6 py-16 text-center">
          <p className="text-sm text-muted-foreground">Sign in to view Usage & Costs.</p>
          <Link to="/login" className="mt-4 inline-flex text-sm font-medium text-primary">
            Log in
          </Link>
        </div>
      </AppShell>
    );
  }

  return (
    <AppShell>
      <div className="mx-auto max-w-3xl px-4 py-8 md:px-6 md:py-10">
        <div className="elevate-hero flex flex-wrap items-start justify-between gap-3">
          <PageHeader
            eyebrow="Account"
            title="Usage & Costs"
            description="Your personal AI usage and spending."
          />
          <button
            type="button"
            onClick={() => void loadSummaryAndFirstPage()}
            className="inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 text-xs hover:bg-accent"
          >
            <RefreshCw className="size-3.5" /> Refresh
          </button>
        </div>

        {error ? (
          <GlassCard className="mt-6 p-5 text-sm text-destructive">{error}</GlassCard>
        ) : null}

        {loading && !summary ? (
          <p className="mt-8 text-sm text-muted-foreground">Loading usage…</p>
        ) : summary ? (
          <>
            <GlassCard
              glow
              className="mt-8 p-6 sm:p-8"
              data-testid="total-spent-card"
            >
              <p className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                Total spent
              </p>
              <p className="mt-2 font-display text-4xl font-semibold tracking-tight sm:text-5xl">
                {formatCost(summary.all_time_usd)}
              </p>
              <p className="mt-2 text-sm text-muted-foreground">
                All tracked AI usage attributed to you
              </p>
            </GlassCard>

            <div className="mt-4 grid gap-3 sm:grid-cols-3">
              <MiniStat label="Today" value={formatCost(summary.today_usd)} />
              <MiniStat label="This month" value={formatCost(summary.month_usd)} />
              <MiniStat
                label="Total tokens"
                value={formatTokensExact(summary.all_time_tokens)}
              />
            </div>

            {summary.historical_notice ? (
              <div className="mt-5 flex gap-2 rounded-xl border border-border/70 bg-muted/20 px-3 py-2.5 text-xs text-muted-foreground">
                <Info className="mt-0.5 size-3.5 shrink-0 opacity-70" aria-hidden />
                <p>
                  Your total includes historical AI usage previously tracked by MultiMind. Some
                  older operations may not have been recorded before complete tracking was
                  enabled.
                </p>
              </div>
            ) : null}

            <section className="mt-8">
              <h2 className="text-sm font-semibold">Recent activity</h2>
              {records.length === 0 ? (
                <GlassCard className="mt-3 p-5 text-sm text-muted-foreground">
                  No AI usage recorded yet.
                </GlassCard>
              ) : (
                <ul className="mt-3 space-y-2" data-testid="recent-activity">
                  {records.map((row) => {
                    const activity = friendlyUsageActivity(row.operation, row.kind);
                    const model = friendlyModelLabel(row.model_id);
                    const tokens = formatTokensLabel(row.tokens_total);
                    return (
                      <li key={row.id}>
                        <GlassCard className="p-3.5 sm:p-4">
                          <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0">
                              <p className="text-sm font-medium">
                                {activity}
                                <span className="font-normal text-muted-foreground">
                                  {" "}
                                  · {model}
                                </span>
                              </p>
                              <p className="mt-0.5 text-xs text-muted-foreground">
                                {formatCompactDate(row.recorded_at)} · {tokens}
                              </p>
                            </div>
                            <p className="shrink-0 text-sm font-semibold tabular-nums">
                              {formatCost(row.cost_usd)}
                            </p>
                          </div>
                        </GlassCard>
                      </li>
                    );
                  })}
                </ul>
              )}

              {hasMore ? (
                <button
                  type="button"
                  onClick={() => void loadMore()}
                  disabled={loadingMore}
                  className="mt-4 w-full rounded-xl border border-border py-2.5 text-sm font-medium hover:bg-accent disabled:opacity-50"
                >
                  {loadingMore ? "Loading…" : "Load more"}
                </button>
              ) : null}
            </section>
          </>
        ) : null}
      </div>
    </AppShell>
  );
}

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <GlassCard className="p-4">
      <div className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div className="mt-1 text-lg font-semibold tracking-tight tabular-nums">{value}</div>
    </GlassCard>
  );
}
