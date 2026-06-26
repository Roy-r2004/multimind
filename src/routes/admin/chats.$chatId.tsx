import { createFileRoute, Link } from "@tanstack/react-router";
import { useCallback } from "react";
import { AdminError, AdminLoading, AdminPageFrame, formatDt } from "@/components/admin/AdminUi";
import { GlassCard } from "@/components/cinematic/PageChrome";
import { useAdminData } from "@/hooks/useAdminData";
import { api } from "@/lib/api";

export const Route = createFileRoute("/admin/chats/$chatId")({
  head: () => ({ meta: [{ title: "Chat Inspector — MultiAI Admin" }] }),
  component: AdminChatDetailPage,
});

function AdminChatDetailPage() {
  const { chatId } = Route.useParams();

  const loader = useCallback(
    (auth: { token: string; orgId: string }) => api.admin.chat(auth, chatId),
    [chatId],
  );
  const { data, loading, error } = useAdminData(loader);

  if (loading) return <AdminLoading />;
  if (error || !data) return <AdminError message={error ?? "Chat not found"} />;

  return (
    <AdminPageFrame
      eyebrow="Chat inspector"
      title={data.title}
      description={`${data.creator_name} · ${data.creator_email}`}
      actions={
        <Link to="/admin/chats" className="text-sm text-primary hover:underline">
          ← All chats
        </Link>
      }
    >
      <div className="mb-4 text-sm text-muted-foreground">
        {data.turns.length} turns · Updated {formatDt(data.updated_at)}
      </div>

      <div className="space-y-4">
        {data.turns.map((turn, index) => (
          <GlassCard key={turn.id} className="p-5">
            <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
              <span>Turn {index + 1}</span>
              <span>{formatDt(turn.created_at)}</span>
              <span className="capitalize">{turn.status}</span>
            </div>
            <div className="mt-4">
              <div className="text-xs font-semibold uppercase text-muted-foreground">User</div>
              <p className="mt-1 whitespace-pre-wrap text-sm">{turn.user_message}</p>
            </div>
            {turn.model_answers?.length > 0 && (
              <div className="mt-4 space-y-3">
                <div className="text-xs font-semibold uppercase text-muted-foreground">Model answers</div>
                {turn.model_answers.map((answer) => (
                  <div key={answer.model_id} className="rounded-lg border border-border bg-background/60 p-3">
                    <div className="text-xs font-medium text-primary">{answer.model_name}</div>
                    <p className="mt-2 whitespace-pre-wrap text-sm">{answer.text ?? answer.error_message}</p>
                  </div>
                ))}
              </div>
            )}
            {turn.verdict && (
              <div className="mt-4 rounded-lg border border-primary/20 bg-primary/5 p-3">
                <div className="text-xs font-semibold uppercase text-primary">Verdict</div>
                <p className="mt-2 whitespace-pre-wrap text-sm">{turn.verdict.text}</p>
                {turn.verdict.reason && (
                  <p className="mt-2 text-xs text-muted-foreground">{turn.verdict.reason}</p>
                )}
              </div>
            )}
          </GlassCard>
        ))}
      </div>
    </AdminPageFrame>
  );
}
