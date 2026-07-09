import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import { FolderKanban, Plus } from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { CreateProjectModal } from "@/components/CreateProjectModal";
import { GlassCard, PageHeader } from "@/components/cinematic/PageChrome";
import { useChatStore } from "@/lib/store";

export const Route = createFileRoute("/projects/")({
  head: () => ({ meta: [{ title: "Projects - MultiAI" }] }),
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
              <Link
                key={p.id}
                to="/projects/$id"
                params={{ id: p.id }}
                className="block cursor-pointer rounded-2xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
              >
                <GlassCard className="h-full p-5 transition hover:border-primary/40 hover:shadow-sm">
                  <div className="flex items-center gap-3">
                    <span className="grid size-10 place-items-center rounded-xl bg-primary/15 text-primary">
                      <FolderKanban className="size-5" />
                    </span>
                    <div className="min-w-0">
                      <div className="font-medium">{p.name}</div>
                      {p.description ? (
                        <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">
                          {p.description}
                        </p>
                      ) : (
                        <p className="mt-0.5 text-xs text-muted-foreground">Open project</p>
                      )}
                    </div>
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
