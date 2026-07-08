import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { FolderKanban, MessageSquare, Pencil, Plus, Save, X } from "lucide-react";
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
  const { authHeaders, isAuthenticated, isLoading: authLoading } = useAuth();
  const { setActiveChatId, refreshAll } = useChatStore();
  const [project, setProject] = useState<ApiProjectDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [editName, setEditName] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    if (authLoading) return;

    const auth = authHeaders();
    if (!auth || !isAuthenticated) {
      setLoading(false);
      setError("Sign in to view projects.");
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    async function loadProject() {
      try {
        const data = await api.projects.get(auth!, id);
        if (!cancelled) {
          setProject(data);
          setEditName(data.name);
          setEditDescription(data.description ?? "");
        }
      } catch {
        // Fallback when GET /projects/:id is unavailable — compose from list endpoints.
        try {
          const [projectList, chatList] = await Promise.all([
            api.projects.list(auth!),
            api.chats.list(auth!),
          ]);
          const summary = projectList.find((p) => p.id === id);
          if (!summary) {
            if (!cancelled) setError("Project not found.");
            return;
          }
          const chats = chatList
            .filter((c) => c.project_id === id)
            .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());
          const data: ApiProjectDetail = {
            ...summary,
            chat_count: chats.length,
            chats,
          };
          if (!cancelled) {
            setProject(data);
            setEditName(data.name);
            setEditDescription(data.description ?? "");
          }
        } catch (e) {
          if (!cancelled) {
            setError(e instanceof Error ? e.message : "Failed to load project");
          }
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void loadProject();
    return () => {
      cancelled = true;
    };
  }, [id, authHeaders, isAuthenticated, authLoading]);

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
        <div className="mx-auto max-w-lg px-6 py-20 text-center">
          <p className="text-sm text-muted-foreground">{error ?? "Project not found."}</p>
          <Link to="/projects" className="mt-4 inline-block text-sm font-medium text-primary hover:underline">
            ← Back to projects
          </Link>
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
    await refreshAll();
    await navigate({ to: "/chat" });
  }

  function startEditing() {
    setEditName(project!.name);
    setEditDescription(project!.description ?? "");
    setSaveError(null);
    setEditing(true);
  }

  function cancelEditing() {
    setEditName(project!.name);
    setEditDescription(project!.description ?? "");
    setSaveError(null);
    setEditing(false);
  }

  async function saveProject() {
    const auth = authHeaders();
    if (!auth) return;
    const name = editName.trim();
    if (!name) {
      setSaveError("Project name is required.");
      return;
    }
    setSaving(true);
    setSaveError(null);
    try {
      const updated = await api.projects.update(auth, project!.id, {
        name,
        description: editDescription.trim() || null,
      });
      setProject(updated);
      setEditing(false);
      await refreshAll();
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : "Failed to save project");
    } finally {
      setSaving(false);
    }
  }

  return (
    <AppShell>
      <div className="mx-auto max-w-4xl px-6 py-10">
        <Link to="/projects" className="text-sm text-muted-foreground hover:text-foreground">
          ← Projects
        </Link>

        {editing ? (
          <div className="mt-4 space-y-4">
            <label className="block text-sm">
              <div className="mb-1 font-medium">Project name</div>
              <input
                autoFocus
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
                className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary"
              />
            </label>
            <label className="block text-sm">
              <div className="mb-1 font-medium">Description</div>
              <textarea
                value={editDescription}
                onChange={(e) => setEditDescription(e.target.value)}
                rows={4}
                className="w-full resize-none rounded-xl border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary"
              />
            </label>
            {saveError && <p className="text-sm text-destructive">{saveError}</p>}
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => void saveProject()}
                disabled={saving}
                className="inline-flex items-center gap-1.5 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
              >
                <Save className="size-4" /> {saving ? "Saving…" : "Save"}
              </button>
              <button
                type="button"
                onClick={cancelEditing}
                disabled={saving}
                className="inline-flex items-center gap-1.5 rounded-xl border border-border px-4 py-2 text-sm hover:bg-accent disabled:opacity-50"
              >
                <X className="size-4" /> Cancel
              </button>
            </div>
          </div>
        ) : (
          <PageHeader
            className="mt-4"
            title={project.name}
            description={
              project.description?.trim() ||
              "No description yet — click Edit to add one."
            }
            action={
              <button
                type="button"
                onClick={startEditing}
                className="inline-flex items-center gap-1.5 rounded-xl border border-border px-3 py-2 text-sm font-medium hover:bg-accent"
              >
                <Pencil className="size-4" /> Edit
              </button>
            }
          />
        )}

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
                className="block w-full cursor-pointer text-left"
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
