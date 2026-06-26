import { createFileRoute, Link } from "@tanstack/react-router";
import { useCallback, useState } from "react";
import {
  AdminError,
  AdminLoading,
  AdminPageFrame,
  DataTable,
  formatDt,
} from "@/components/admin/AdminUi";
import { GlassCard } from "@/components/cinematic/PageChrome";
import { useAdminData } from "@/hooks/useAdminData";
import { api } from "@/lib/api";

export const Route = createFileRoute("/admin/chats")({
  head: () => ({ meta: [{ title: "Chats — MultiAI Admin" }] }),
  component: AdminChatsPage,
});

function AdminChatsPage() {
  const [q, setQ] = useState("");

  const loader = useCallback(
    (auth: { token: string; orgId: string }) => api.admin.chats(auth, { q: q || undefined }),
    [q],
  );
  const { data, loading, error, reload } = useAdminData(loader);

  if (loading && !data) return <AdminLoading />;
  if (error && !data) return <AdminError message={error} />;

  return (
    <AdminPageFrame
      title="Chats"
      description="Read every conversation in your organization — full message and verdict history."
    >
      <GlassCard className="mb-4 p-4">
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void reload();
          }}
        >
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search by title, user name, or email…"
            className="flex-1 rounded-lg border border-border bg-background px-3 py-2 text-sm"
          />
          <button type="submit" className="rounded-lg bg-primary px-4 py-2 text-sm text-primary-foreground">
            Search
          </button>
        </form>
      </GlassCard>

      <DataTable
        columns={[
          { key: "title", label: "Chat" },
          { key: "creator", label: "Creator" },
          { key: "turns", label: "Turns" },
          { key: "updated", label: "Updated" },
          { key: "actions", label: "" },
        ]}
        rows={(data ?? []).map((chat) => ({
          id: chat.id,
          cells: {
            title: chat.title,
            creator: (
              <div>
                <div>{chat.creator_name}</div>
                <div className="text-xs text-muted-foreground">{chat.creator_email}</div>
              </div>
            ),
            turns: chat.turn_count,
            updated: formatDt(chat.updated_at),
            actions: (
              <Link
                to="/admin/chats/$chatId"
                params={{ chatId: chat.id }}
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
