import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import { FolderKanban, MessageSquare, Users, FileText, LayoutGrid, Settings } from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { PROJECTS, SAMPLE_CHATS, MODEL_SETS } from "@/lib/mock";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/projects/$id")({
  head: () => ({ meta: [{ title: "Project — MultiAI" }] }),
  component: ProjectDetail,
});

function ProjectDetail() {
  const { id } = Route.useParams();
  const p = PROJECTS.find((x) => x.id === id) ?? PROJECTS[0];
  const [tab, setTab] = useState<"chats" | "files" | "sets" | "settings">("chats");
  const tabs = [
    { id: "chats", label: "Chats", icon: MessageSquare },
    { id: "files", label: "Files", icon: FileText },
    { id: "sets", label: "Model Sets", icon: LayoutGrid },
    { id: "settings", label: "Settings", icon: Settings },
  ] as const;

  return (
    <AppShell>
      <div className="mx-auto max-w-5xl px-6 py-10">
        <Link to="/projects" className="text-sm text-muted-foreground hover:text-foreground">
          ← All projects
        </Link>
        <div className="mt-2 flex items-center gap-3">
          <span className="grid size-10 place-items-center rounded-xl bg-accent text-accent-foreground">
            <FolderKanban className="size-5" />
          </span>
          <div className="flex-1 min-w-0">
            <h1 className="truncate text-2xl font-semibold">{p.name}</h1>
            <div className="text-xs text-muted-foreground">
              {p.chats} chats ·{" "}
              <span className="inline-flex items-center gap-1">
                <Users className="size-3" /> {p.members} members
              </span>{" "}
              · Updated {p.updated}
            </div>
          </div>
        </div>

        <div className="mt-6 border-b border-border">
          <div className="flex gap-1 overflow-x-auto">
            {tabs.map((t) => (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                className={cn(
                  "inline-flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm",
                  tab === t.id
                    ? "border-primary text-foreground"
                    : "border-transparent text-muted-foreground hover:text-foreground",
                )}
              >
                <t.icon className="size-3.5" /> {t.label}
              </button>
            ))}
          </div>
        </div>

        <div className="mt-6">
          {tab === "chats" && (
            <div className="space-y-2">
              {SAMPLE_CHATS.map((c) => (
                <Link
                  key={c.id}
                  to="/chat"
                  className="flex items-center justify-between rounded-xl border border-border bg-card p-3 hover:bg-accent"
                >
                  <div className="text-sm font-medium">{c.title}</div>
                  <div className="text-xs text-muted-foreground">{c.updated}</div>
                </Link>
              ))}
            </div>
          )}
          {tab === "files" && (
            <div className="grid place-items-center rounded-2xl border border-dashed border-border bg-card p-12 text-center text-sm text-muted-foreground">
              No files in this project yet.
            </div>
          )}
          {tab === "sets" && (
            <div className="grid gap-3 md:grid-cols-2">
              {MODEL_SETS.slice(0, 2).map((s) => (
                <div key={s.id} className="rounded-xl border border-border bg-card p-4">
                  <div className="font-medium">{s.name}</div>
                  <div className="text-xs text-muted-foreground">{s.description}</div>
                </div>
              ))}
            </div>
          )}
          {tab === "settings" && (
            <div className="rounded-2xl border border-border bg-card p-5 text-sm text-muted-foreground">
              Project settings (rename, members, archive) go here.
            </div>
          )}
        </div>
      </div>
    </AppShell>
  );
}
