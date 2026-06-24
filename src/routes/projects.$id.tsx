import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import { FolderKanban, MessageSquare } from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { GlassCard, PageHeader } from "@/components/cinematic/PageChrome";
import { useChatStore } from "@/lib/store";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/projects/$id")({
  head: () => ({ meta: [{ title: "Project — MultiAI" }] }),
  component: ProjectDetail,
});

function ProjectDetail() {
  const { id } = Route.useParams();
  const { projects, chats, projectChatCount, setActiveChatId } = useChatStore();
  const p = projects.find((x) => x.id === id);
  const projectChats = chats.filter((c) => c.projectId === id);

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
        />

        <div className="mt-8 space-y-2">
          {projectChats.length === 0 ? (
            <GlassCard className="p-10 text-center text-sm text-muted-foreground">
              No chats in this project. Assign chats from the sidebar menu.
            </GlassCard>
          ) : (
            projectChats.map((c) => (
              <Link
                key={c.id}
                to="/chat"
                onClick={() => setActiveChatId(c.id)}
                className="block"
              >
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
    </AppShell>
  );
}
