import { createFileRoute, Link } from "@tanstack/react-router";
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

export const Route = createFileRoute("/admin/brains")({
  head: () => ({ meta: [{ title: "User Brains — MultiAI Admin" }] }),
  component: AdminBrainsPage,
});

function AdminBrainsPage() {
  const loader = useCallback(
    (auth: { token: string; orgId: string }) => api.admin.brains(auth),
    [],
  );
  const { data, loading, error } = useAdminData(loader);

  if (loading) return <AdminLoading />;
  if (error) return <AdminError message={error} />;

  return (
    <AdminPageFrame
      title="User Brains"
      description="Persistent memory profiles learned from verdict challenges — what each user thinks."
    >
      <DataTable
        columns={[
          { key: "user", label: "User" },
          { key: "summary", label: "Summary" },
          { key: "lessons", label: "Lessons" },
          { key: "memories", label: "Memories" },
          { key: "updated", label: "Updated" },
          { key: "actions", label: "" },
        ]}
        rows={(data ?? []).map((brain) => ({
          id: brain.user_id,
          cells: {
            user: (
              <div>
                <div className="font-medium">{brain.user_name}</div>
                <div className="text-xs text-muted-foreground">{brain.email}</div>
              </div>
            ),
            summary: <span className="line-clamp-2 max-w-md text-xs">{brain.summary || "—"}</span>,
            lessons: brain.lesson_count,
            memories: brain.memories_count,
            updated: formatDt(brain.updated_at),
            actions: (
              <Link
                to="/admin/users/$userId"
                params={{ userId: brain.user_id }}
                className="text-sm text-primary hover:underline"
              >
                Full profile →
              </Link>
            ),
          },
        }))}
        empty="No brain profiles yet. Brains are created when users challenge verdicts."
      />
    </AdminPageFrame>
  );
}
