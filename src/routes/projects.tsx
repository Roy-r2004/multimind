import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import { Plus, FolderKanban } from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { GlassCard, PageHeader } from "@/components/cinematic/PageChrome";
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
        <PageHeader
          eyebrow="Organization"
          title="Projects"
          description="Group related chats for context and cost tracking."
          action={
            <button
              onClick={() => setShowCreate(true)}
              className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground"
            >
              <Plus className="size-4" /> New project
            </button>
          }
        />

        <div className="mt-8 grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {projects.length === 0 ? (
            <GlassCard className="col-span-full p-12 text-center text-sm text-muted-foreground">
              No projects yet. Create one to organize chats.
            </GlassCard>
          ) : (
            projects.map((p) => (
              <Link key={p.id} to="/projects/$id" params={{ id: p.id }}>
                <GlassCard className="p-5 transition hover:border-primary/30">
                  <div className="flex items-center gap-3">
                    <span className="grid size-10 place-items-center rounded-xl bg-primary/15 text-primary">
                      <FolderKanban className="size-5" />
                    </span>
                    <div className="font-medium">{p.name}</div>
                  </div>
                  <div className="mt-4 flex justify-between text-xs text-muted-foreground">
                    <span>{projectChatCount(p.id)} chats</span>
                    <span>{p.updated}</span>
                  </div>
                </GlassCard>
              </Link>
            ))
          )}
        </div>
      </div>
      <CreateProjectModal open={showCreate} onClose={() => setShowCreate(false)} />
    </AppShell>
  );
}
