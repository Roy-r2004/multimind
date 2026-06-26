import { createFileRoute } from "@tanstack/react-router";
import { useCallback } from "react";
import {
  AdminError,
  AdminLoading,
  AdminPageFrame,
  DataTable,
  formatDt,
} from "@/components/admin/AdminUi";
import { useAdminData } from "@/hooks/useAdminData";
import { api } from "@/lib/api";

export const Route = createFileRoute("/admin/security")({
  head: () => ({ meta: [{ title: "Security — MultiAI Admin" }] }),
  component: AdminSecurityPage,
});

function AdminSecurityPage() {
  const loader = useCallback(
    (auth: { token: string; orgId: string }) => api.admin.securityEvents(auth),
    [],
  );
  const { data, loading, error } = useAdminData(loader);

  if (loading) return <AdminLoading />;
  if (error) return <AdminError message={error} />;

  return (
    <AdminPageFrame
      title="Security"
      description="Authentication events — sign-ins, failures, and session activity."
    >
      <DataTable
        columns={[
          { key: "time", label: "Time" },
          { key: "action", label: "Action" },
          { key: "actor", label: "Actor" },
          { key: "ip", label: "IP" },
          { key: "summary", label: "Summary" },
        ]}
        rows={(data?.items ?? []).map((log) => ({
          id: log.id,
          cells: {
            time: formatDt(log.created_at),
            action: <code className="text-xs">{log.action}</code>,
            actor: log.actor_email || "—",
            ip: log.ip_address ?? "—",
            summary: log.summary,
          },
        }))}
        empty="No security events recorded yet."
      />
    </AdminPageFrame>
  );
}
