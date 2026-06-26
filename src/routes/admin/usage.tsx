import { createFileRoute } from "@tanstack/react-router";
import { useCallback } from "react";
import {
  AdminError,
  AdminLoading,
  AdminPageFrame,
  formatUsd,
  StatCard,
} from "@/components/admin/AdminUi";
import { GlassCard } from "@/components/cinematic/PageChrome";
import { useAdminData } from "@/hooks/useAdminData";
import { api } from "@/lib/api";

export const Route = createFileRoute("/admin/usage")({
  head: () => ({ meta: [{ title: "Usage — MultiAI Admin" }] }),
  component: AdminUsagePage,
});

function AdminUsagePage() {
  const loader = useCallback(async (auth: { token: string; orgId: string }) => {
    const [overview, usage] = await Promise.all([
      api.admin.overview(auth),
      api.admin.usage(auth),
    ]);
    return { overview, usage };
  }, []);

  const { data, loading, error } = useAdminData(loader);

  if (loading) return <AdminLoading />;
  if (error || !data) return <AdminError message={error ?? "Failed to load usage"} />;

  const { overview, usage } = data;
  const budgetUsed = Math.min(100, Math.max(0, usage.budget_used_pct));

  return (
    <AdminPageFrame title="Usage & Billing" description="Spending, tokens, and budget utilization.">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Today" value={formatUsd(usage.today_usd)} />
        <StatCard label="This month" value={formatUsd(usage.month_usd)} />
        <StatCard label="Budget" value={formatUsd(overview.monthly_budget_usd)} />
        <StatCard label="Month tokens" value={usage.month_tokens.toLocaleString()} />
      </div>

      <GlassCard className="mt-6 p-5">
        <div className="mb-2 flex justify-between text-sm">
          <span>Budget progress</span>
          <span>{budgetUsed.toFixed(1)}%</span>
        </div>
        <div className="h-3 overflow-hidden rounded-full bg-muted">
          <div
            className={`h-full rounded-full ${budgetUsed >= 90 ? "bg-destructive" : budgetUsed >= 70 ? "bg-amber-500" : "bg-primary"}`}
            style={{ width: `${budgetUsed}%` }}
          />
        </div>
      </GlassCard>

      <GlassCard className="mt-6 p-5">
        <h2 className="font-semibold">By model</h2>
        <table className="mt-4 w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase text-muted-foreground">
              <th className="pb-2">Model</th>
              <th className="pb-2">Cost</th>
              <th className="pb-2">Tokens</th>
            </tr>
          </thead>
          <tbody>
            {usage.by_model.map((row) => (
              <tr key={row.model_id} className="border-t border-border">
                <td className="py-2 font-mono text-xs">{row.model_id}</td>
                <td className="py-2">{formatUsd(row.cost_usd)}</td>
                <td className="py-2">{row.tokens.toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </GlassCard>
    </AdminPageFrame>
  );
}
