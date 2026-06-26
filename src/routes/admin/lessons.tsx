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

export const Route = createFileRoute("/admin/lessons")({
  head: () => ({ meta: [{ title: "Lessons — MultiAI Admin" }] }),
  component: AdminLessonsPage,
});

function AdminLessonsPage() {
  const loader = useCallback(
    (auth: { token: string; orgId: string }) => api.admin.lessons(auth),
    [],
  );
  const { data, loading, error } = useAdminData(loader);

  if (loading) return <AdminLoading />;
  if (error) return <AdminError message={error} />;

  return (
    <AdminPageFrame
      title="Verdict Lessons"
      description="Structured lessons built when users disagreed with AI verdicts."
    >
      <DataTable
        columns={[
          { key: "title", label: "Lesson" },
          { key: "user", label: "User" },
          { key: "status", label: "Status" },
          { key: "created", label: "Created" },
          { key: "actions", label: "" },
        ]}
        rows={(data ?? []).map((lesson) => ({
          id: lesson.id,
          cells: {
            title: (
              <div>
                <div className="font-medium">{lesson.title}</div>
                <div className="line-clamp-1 text-xs text-muted-foreground">{lesson.summary}</div>
              </div>
            ),
            user: lesson.user_name,
            status: <span className="capitalize">{lesson.status}</span>,
            created: formatDt(lesson.created_at),
            actions: (
              <Link
                to="/admin/users/$userId"
                params={{ userId: lesson.user_id }}
                className="text-sm text-primary hover:underline"
              >
                User →
              </Link>
            ),
          },
        }))}
        empty="No lessons yet."
      />
    </AdminPageFrame>
  );
}
