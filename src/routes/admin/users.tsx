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

export const Route = createFileRoute("/admin/users")({
  head: () => ({ meta: [{ title: "Users — MultiAI Admin" }] }),
  component: AdminUsersPage,
});

function AdminUsersPage() {
  const loader = useCallback(
    (auth: { token: string; orgId: string }) => api.admin.users(auth),
    [],
  );
  const { data, loading, error } = useAdminData(loader);

  if (loading) return <AdminLoading />;
  if (error) return <AdminError message={error} />;

  return (
    <AdminPageFrame
      title="Users"
      description="Every member in your organization — activity, chats, and brain memory."
    >
      <DataTable
        columns={[
          { key: "name", label: "User" },
          { key: "role", label: "Role" },
          { key: "chats", label: "Chats" },
          { key: "turns", label: "Turns" },
          { key: "brain", label: "Brain" },
          { key: "joined", label: "Joined" },
          { key: "actions", label: "" },
        ]}
        rows={(data ?? []).map((user) => ({
          id: user.user_id,
          cells: {
            name: (
              <div>
                <div className="font-medium">{user.full_name}</div>
                <div className="text-xs text-muted-foreground">{user.email}</div>
              </div>
            ),
            role: <span className="capitalize">{user.role}</span>,
            chats: user.chat_count,
            turns: user.turn_count,
            brain: user.has_brain ? `${user.brain_lesson_count} lessons` : "—",
            joined: formatDt(user.joined_at),
            actions: (
              <Link
                to="/admin/users/$userId"
                params={{ userId: user.user_id }}
                className="text-sm font-medium text-primary hover:underline"
              >
                Inspect →
              </Link>
            ),
          },
        }))}
      />
    </AdminPageFrame>
  );
}
