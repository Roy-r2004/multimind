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

export const Route = createFileRoute("/admin/organization")({
  head: () => ({ meta: [{ title: "Organization — MultiAI Admin" }] }),
  component: AdminOrganizationPage,
});

function AdminOrganizationPage() {
  const loader = useCallback(
    (auth: { token: string; orgId: string }) => api.admin.overview(auth),
    [],
  );
  const { data, loading, error } = useAdminData(loader);

  if (loading) return <AdminLoading />;
  if (error || !data) return <AdminError message={error ?? "Failed to load organization"} />;

  return (
    <AdminPageFrame title="Organization" description="Organization profile and workspace totals.">
      <GlassCard className="p-6">
        <h2 className="text-2xl font-semibold">{data.organization_name}</h2>
        <p className="mt-1 text-sm text-muted-foreground">Slug: {data.organization_slug}</p>
        <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <StatCard label="Plan" value={data.plan} />
          <StatCard label="Your role" value={data.user_role} />
          <StatCard label="Monthly budget" value={formatUsd(data.monthly_budget_usd)} />
          <StatCard label="Members" value={data.total_members} />
          <StatCard label="Projects" value={data.total_projects} />
          <StatCard label="Chats" value={data.total_chats} />
        </div>
      </GlassCard>
    </AdminPageFrame>
  );
}
