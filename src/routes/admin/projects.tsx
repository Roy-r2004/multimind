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

export const Route = createFileRoute("/admin/projects")({
  head: () => ({ meta: [{ title: "Projects — MultiAI Admin" }] }),
  component: AdminProjectsPage,
});

function AdminProjectsPage() {
  const loader = useCallback(
    (auth: { token: string; orgId: string }) => api.admin.projects(auth),
    [],
  );
  const { data, loading, error } = useAdminData(loader);

  if (loading) return <AdminLoading />;
  if (error) return <AdminError message={error} />;

  return (
    <AdminPageFrame title="Projects" description="All projects and their chat counts.">
      <DataTable
        columns={[
          { key: "name", label: "Project" },
          { key: "chats", label: "Chats" },
          { key: "created", label: "Created" },
        ]}
        rows={(data ?? []).map((p) => ({
          id: p.id,
          cells: {
            name: (
              <div>
                <div className="font-medium">{p.name}</div>
                {p.description && (
                  <div className="text-xs text-muted-foreground">{p.description}</div>
                )}
              </div>
            ),
            chats: p.chat_count,
            created: formatDt(p.created_at),
          },
        }))}
      />
    </AdminPageFrame>
  );
}
