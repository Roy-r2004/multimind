import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { MessageSquare, Trash2 } from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { GlassCard, PageHeader } from "@/components/cinematic/PageChrome";
import { Modal } from "@/components/Modal";
import { useChatStore } from "@/lib/store";

export const Route = createFileRoute("/projects/$id")({
  head: () => ({ meta: [{ title: "Project — MultiAI" }] }),
  component: ProjectDetail,
});

function ProjectDetail() {
  const { id } = Route.useParams();
  const navigate = useNavigate();
  const { projects, chats, projectChatCount, setActiveChatId, deleteProject } = useChatStore();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const p = projects.find((x) => x.id === id);
  const projectChats = chats.filter((c) => c.projectId === id);

  async function confirmDeleteProject() {
    if (!p) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteProject(p.id);
      setConfirmDelete(false);
      void navigate({ to: "/projects" });
    } catch (e) {
      setDeleteError(e instanceof Error ? e.message : "Failed to delete project");
    } finally {
      setDeleting(false);
    }
  }

  if (!p) {
    return (
      <AppShell>
        <div className="px-6 py-20 text-center text-muted-foreground">Project not found.</div>
      </AppShell>
    );
  }

  return (
    <AppShell>
      <div className="mx-auto max-w-4xl px-6 py-10">
        <Link to="/projects" className="text-sm text-muted-foreground hover:text-foreground">
          ← Projects
        </Link>
        <PageHeader
          className="mt-4"
          title={p.name}
          description={`${projectChatCount(p.id)} chats · Updated ${p.updated}`}
          action={
            <button
              type="button"
              onClick={() => setConfirmDelete(true)}
              className="inline-flex items-center gap-2 rounded-xl border border-destructive/30 px-4 py-2.5 text-sm font-medium text-destructive hover:bg-destructive/10"
            >
              <Trash2 className="size-4" /> Delete project
            </button>
          }
        />

        <div className="mt-8 space-y-2">
          {projectChats.length === 0 ? (
            <GlassCard className="p-10 text-center text-sm text-muted-foreground">
              No chats in this project yet.
            </GlassCard>
          ) : (
            projectChats.map((c) => (
              <Link key={c.id} to="/chat" onClick={() => setActiveChatId(c.id)} className="block">
                <GlassCard className="flex items-center justify-between p-4 hover:border-primary/30">
                  <div className="flex items-center gap-3">
                    <MessageSquare className="size-4 text-primary" />
                    <span className="text-sm font-medium">{c.title}</span>
                  </div>
                  <span className="text-xs text-muted-foreground">{c.updated}</span>
                </GlassCard>
              </Link>
            ))
          )}
        </div>
      </div>
      <Modal
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        title="Delete project?"
        size="sm"
      >
        <p className="text-sm text-muted-foreground">
          Chats in this project will stay in your chat history.
        </p>
        {deleteError && <p className="mt-2 text-sm text-destructive">{deleteError}</p>}
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={() => setConfirmDelete(false)}
            disabled={deleting}
            className="rounded-lg border border-border px-4 py-2 text-sm hover:bg-accent disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={deleting}
            onClick={() => void confirmDeleteProject()}
            className="rounded-lg bg-destructive px-4 py-2 text-sm font-medium text-destructive-foreground disabled:opacity-50"
          >
            {deleting ? "Deleting..." : "Delete"}
          </button>
        </div>
      </Modal>
    </AppShell>
  );
}
