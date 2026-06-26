import { createFileRoute, Link } from "@tanstack/react-router";
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

export const Route = createFileRoute("/admin/")({
  head: () => ({ meta: [{ title: "Admin Dashboard — MultiAI" }] }),
  component: AdminDashboardPage,
});

function AdminDashboardPage() {
  const loader = useCallback(async (auth: { token: string; orgId: string }) => {
    const [overview, usage, auditStats] = await Promise.all([
      api.admin.overview(auth),
      api.admin.usage(auth),
      api.admin.auditStats(auth),
    ]);
    return { overview, usage, auditStats };
  }, []);

  const { data, loading, error } = useAdminData(loader);

  if (loading) return <AdminLoading />;
  if (error || !data) return <AdminError message={error ?? "Failed to load dashboard"} />;

  const { overview, usage, auditStats } = data;

  return (
    <AdminPageFrame
      title="Dashboard"
      description="Organization overview, spending, and audit activity at a glance."
    >
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Members" value={overview.total_members} />
        <StatCard label="Chats" value={overview.total_chats} />
        <StatCard label="Audit events (24h)" value={auditStats.last_24h} />
        <StatCard
          label="Budget used"
          value={`${usage.budget_used_pct.toFixed(1)}%`}
          hint={formatUsd(usage.month_usd)}
        />
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <GlassCard className="p-5">
          <h2 className="font-semibold">Workspace</h2>
          <div className="mt-4 grid grid-cols-2 gap-3 text-sm">
            <div>
              <div className="text-muted-foreground">Projects</div>
              <div className="text-xl font-semibold">{overview.total_projects}</div>
            </div>
            <div>
              <div className="text-muted-foreground">Model sets</div>
              <div className="text-xl font-semibold">{overview.total_model_sets}</div>
            </div>
            <div>
              <div className="text-muted-foreground">Templates</div>
              <div className="text-xl font-semibold">{overview.total_templates}</div>
            </div>
            <div>
              <div className="text-muted-foreground">Turns</div>
              <div className="text-xl font-semibold">{usage.total_turns}</div>
            </div>
          </div>
        </GlassCard>

        <GlassCard className="p-5">
          <h2 className="font-semibold">Audit trail</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            {auditStats.total.toLocaleString()} total events · {auditStats.critical} critical
          </p>
          <ul className="mt-4 space-y-2 text-sm">
            {auditStats.top_actions.slice(0, 5).map((item) => (
              <li key={item.action} className="flex justify-between gap-2">
                <span className="truncate font-mono text-xs">{item.action}</span>
                <span className="text-muted-foreground">{item.count}</span>
              </li>
            ))}
          </ul>
          <Link
            to="/admin/audit"
            className="mt-4 inline-flex text-sm font-medium text-primary hover:underline"
          >
            View full audit logs →
          </Link>
        </GlassCard>
      </div>

      <div className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {[
          { to: "/admin/users", label: "Inspect users" },
          { to: "/admin/chats", label: "Read all chats" },
          { to: "/admin/brains", label: "User brains" },
          { to: "/admin/security", label: "Security events" },
        ].map((link) => (
          <Link
            key={link.to}
            to={link.to}
            className="rounded-xl border border-border bg-card/80 px-4 py-3 text-sm font-medium hover:bg-accent"
          >
            {link.label}
          </Link>
        ))}
      </div>
    </AdminPageFrame>
  );
}
