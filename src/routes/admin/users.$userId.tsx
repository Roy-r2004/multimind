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

export const Route = createFileRoute("/admin/users/$userId")({
  head: () => ({ meta: [{ title: "User Detail — MultiAI Admin" }] }),
  component: AdminUserDetailPage,
});

function AdminUserDetailPage() {
  const { userId } = Route.useParams();
  const [tab, setTab] = useState<"overview" | "chats" | "brain" | "activity">("overview");

  const loader = useCallback(
    async (auth: { token: string; orgId: string }) => {
      const [user, chats, activity] = await Promise.all([
        api.admin.user(auth, userId),
        api.admin.userChats(auth, userId),
        api.admin.userActivity(auth, userId),
      ]);
      return { user, chats, activity };
    },
    [userId],
  );

  const { data, loading, error } = useAdminData(loader);

  if (loading) return <AdminLoading />;
  if (error || !data) return <AdminError message={error ?? "User not found"} />;

  const { user, chats, activity } = data;

  return (
    <AdminPageFrame
      eyebrow="Users"
      title={user.full_name}
      description={user.email}
      actions={
        <Link to="/admin/users" className="text-sm text-primary hover:underline">
          ← All users
        </Link>
      }
    >
      <div className="mb-6 flex flex-wrap gap-2">
        {(["overview", "chats", "brain", "activity"] as const).map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => setTab(t)}
            className={[
              "rounded-xl px-3 py-1.5 text-sm capitalize",
              tab === t ? "bg-primary text-primary-foreground" : "border border-border hover:bg-accent",
            ].join(" ")}
          >
            {t}
          </button>
        ))}
      </div>

      {tab === "overview" && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <GlassCard className="p-4">
            <div className="text-xs text-muted-foreground">Role</div>
            <div className="mt-1 capitalize font-semibold">{user.role}</div>
          </GlassCard>
          <GlassCard className="p-4">
            <div className="text-xs text-muted-foreground">Chats</div>
            <div className="mt-1 text-2xl font-semibold">{user.chat_count}</div>
          </GlassCard>
          <GlassCard className="p-4">
            <div className="text-xs text-muted-foreground">Turns</div>
            <div className="mt-1 text-2xl font-semibold">{user.turn_count}</div>
          </GlassCard>
          <GlassCard className="p-4">
            <div className="text-xs text-muted-foreground">Lessons</div>
            <div className="mt-1 text-2xl font-semibold">{user.lesson_count}</div>
          </GlassCard>
        </div>
      )}

      {tab === "chats" && (
        <DataTable
          columns={[
            { key: "title", label: "Title" },
            { key: "turns", label: "Turns" },
            { key: "updated", label: "Updated" },
            { key: "actions", label: "" },
          ]}
          rows={chats.map((chat) => ({
            id: chat.id,
            cells: {
              title: chat.title,
              turns: chat.turn_count,
              updated: formatDt(chat.updated_at),
              actions: (
                <Link
                  to="/admin/chats/$chatId"
                  params={{ chatId: chat.id }}
                  className="text-sm text-primary hover:underline"
                >
                  Read chat →
                </Link>
              ),
            },
          }))}
          empty="No chats for this user."
        />
      )}

      {tab === "brain" && (
        <GlassCard className="p-5 space-y-4">
          <div>
            <h3 className="font-semibold">Thinking style</h3>
            <p className="mt-2 text-sm text-muted-foreground whitespace-pre-wrap">
              {user.brain.thinking_style || "No brain profile yet."}
            </p>
          </div>
          <div>
            <h3 className="font-semibold">Summary</h3>
            <p className="mt-2 text-sm">{user.brain.summary || "—"}</p>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <h4 className="text-sm font-medium">Likes</h4>
              <ul className="mt-2 list-disc pl-5 text-sm text-muted-foreground">
                {(user.brain.likes.length ? user.brain.likes : ["—"]).map((l) => (
                  <li key={l}>{l}</li>
                ))}
              </ul>
            </div>
            <div>
              <h4 className="text-sm font-medium">Dislikes</h4>
              <ul className="mt-2 list-disc pl-5 text-sm text-muted-foreground">
                {(user.brain.dislikes.length ? user.brain.dislikes : ["—"]).map((d) => (
                  <li key={d}>{d}</li>
                ))}
              </ul>
            </div>
          </div>
          {user.brain.memories.length > 0 && (
            <div>
              <h4 className="text-sm font-medium">Memories ({user.brain.memories.length})</h4>
              <pre className="mt-2 max-h-64 overflow-auto rounded-lg bg-muted/50 p-3 text-xs">
                {JSON.stringify(user.brain.memories, null, 2)}
              </pre>
            </div>
          )}
        </GlassCard>
      )}

      {tab === "activity" && (
        <DataTable
          columns={[
            { key: "time", label: "Time" },
            { key: "action", label: "Action" },
            { key: "summary", label: "Summary" },
          ]}
          rows={activity.items.map((log) => ({
            id: log.id,
            cells: {
              time: formatDt(log.created_at),
              action: <code className="text-xs">{log.action}</code>,
              summary: log.summary,
            },
          }))}
          empty="No activity recorded for this user yet."
        />
      )}
    </AdminPageFrame>
  );
}
