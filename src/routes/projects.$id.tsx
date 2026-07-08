import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { FolderKanban, MessageSquare, Plus } from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { GlassCard, PageHeader } from "@/components/cinematic/PageChrome";
import { api } from "@/lib/api";
import type { ApiProjectDetail } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";
import { useChatStore } from "@/lib/store";

export const Route = createFileRoute("/projects/$id")({
  head: () => ({ meta: [{ title: "Project — MultiAI" }] }),
  component: ProjectDetail,
});

function formatRelativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `${mins || 1}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

function ProjectDetail() {
  const { id } = Route.useParams();
  const navigate = useNavigate();
  const { authHeaders, isAuthenticated } = useAuth();
  const { createChat } = useChatStore();
  const [project, setProject] = useState<ApiProjectDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const auth = authHeaders();
    if (!auth || !isAuthenticated) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    void api.projects
      .get(auth, id)
      .then((data) => {
        if (!cancelled) setProject(data);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load project");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [id, authHeaders, isAuthenticated]);

  if (loading) {
    return (
      <AppShell>
        <div className="px-6 py-20 text-center text-sm text-muted-foreground">Loading project…</div>
      </AppShell>
    );
  }

  if (error || !project) {
    return (
      <AppShell>
        <div className="px-6 py-20 text-center text-muted-foreground">
          {error ?? "Project not found."}
        </div>
      </AppShell>
    );
  }

  async function openChat(chatId: string) {
    setActiveChatId(chatId);
    await navigate({ to: "/chat" });
  }

  async function startChatInProject() {
    const auth = authHeaders();
    if (!auth) return;
    const chat = await api.chats.create(auth, { title: "New chat", project_id: project!.id });
    setActiveChatId(chat.id);
    await navigate({ to: "/chat" });
  }

  return (
    <AppShell>
      <div className="mx-auto max-w-4xl px-6 py-10">
        <Link to="/projects" className="text-sm text-muted-foreground hover:text-foreground">
          ← Projects
        </Link>

        <PageHeader
          className="mt-4"
          title={project.name}
          description={
            project.description?.trim() ||
            "No description yet — use this project to group related chats."
          }
        />

        <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
          <span className="inline-flex items-center gap-1.5 rounded-full border border-border px-2.5 py-1">
            <FolderKanban className="size-3.5 text-primary" />
            {project.chat_count} chat{project.chat_count === 1 ? "" : "s"}
          </span>
          <span>Updated {formatRelativeTime(project.updated_at)}</span>
        </div>

        <div className="mt-8 flex items-center justify-between">
          <h2 className="text-sm font-semibold">Chats in this project</h2>
          <button
            type="button"
            onClick={() => void startChatInProject()}
            className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs font-medium hover:bg-accent"
          >
            <Plus className="size-3.5" /> New chat
          </button>
        </div>

        <div className="mt-4 space-y-2">
          {project.chats.length === 0 ? (
            <GlassCard className="p-10 text-center text-sm text-muted-foreground">
              No chats yet. Start one here or assign existing chats from the sidebar.
            </GlassCard>
          ) : (
            project.chats.map((c) => (
              <button
                key={c.id}
                type="button"
                onClick={() => void openChat(c.id)}
                className="block w-full text-left"
              >
                <GlassCard className="flex items-center justify-between p-4 transition hover:border-primary/30">
                  <div className="flex items-center gap-3">
                    <MessageSquare className="size-4 text-primary" />
                    <span className="text-sm font-medium">{c.title}</span>
                  </div>
                  <span className="text-xs text-muted-foreground">
                    {formatRelativeTime(c.updated_at)}
                  </span>
                </GlassCard>
              </button>
            ))
          )}
        </div>
      </div>
    </AppShell>
  );
}
