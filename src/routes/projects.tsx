import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import { Plus, FolderKanban, Users } from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { CreateProjectModal } from "@/components/CreateProjectModal";
import { useChatStore } from "@/lib/store";

export const Route = createFileRoute("/projects")({
  head: () => ({ meta: [{ title: "Projects — MultiAI" }] }),
  component: ProjectsPage,
});

function ProjectsPage() {
  const { projects, projectChatCount } = useChatStore();
  const [showCreate, setShowCreate] = useState(false);

  return (
    <AppShell>
      <div className="mx-auto max-w-5xl px-6 py-10">
        <div className="flex items-end justify-between">
          <div>
            <h1 className="text-2xl font-semibold">Projects</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Group related chats, files and Model Sets.
            </p>
          </div>
          <button
            onClick={() => setShowCreate(true)}
            className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:opacity-90"
          >
            <Plus className="size-4" /> New project
          </button>
        </div>
        <div className="mt-8 grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {projects.map((p) => (
            <Link
              key={p.id}
              to="/projects/$id"
              params={{ id: p.id }}
              className="block rounded-2xl border border-border bg-card p-5 hover:border-foreground/20"
            >
              <div className="flex items-center gap-2">
                <span className="grid size-9 place-items-center rounded-lg bg-accent text-accent-foreground">
                  <FolderKanban className="size-4" />
                </span>
                <div className="font-medium">{p.name}</div>
              </div>
              <div className="mt-4 flex items-center justify-between text-xs text-muted-foreground">
                <span>{projectChatCount(p.id)} chats</span>
                <span className="inline-flex items-center gap-1">
                  <Users className="size-3" /> {p.members}
                </span>
                <span>{p.updated}</span>
              </div>
            </Link>
          ))}
        </div>
      </div>

      <CreateProjectModal open={showCreate} onClose={() => setShowCreate(false)} />
    </AppShell>
  );
}
