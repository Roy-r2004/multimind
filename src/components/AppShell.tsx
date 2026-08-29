import { Link, useNavigate, useRouterState } from "@tanstack/react-router";
import { useState, type ReactNode, useMemo } from "react";
import {
  BookOpen,
  Brain,
  FolderKanban,
  LayoutGrid,
  Library,
  LogOut,
  Bookmark,
  FileText,
  Map as MapIcon,
  Menu,
  MessageSquare,
  MessageSquareText,
  NotebookTabs,
  Settings,
  X,
} from "lucide-react";
import { BrandLogo } from "@/components/BrandLogo";
import { CinematicBackdrop } from "@/components/cinematic/PageChrome";
import { ScrapingDreamSky } from "@/components/scraping/ScrapingDreamSky";
import { ChatSidebarContent } from "@/components/sidebar/ChatSidebarContent";
import { ScrapingSidebarContent } from "@/components/sidebar/ScrapingSidebarContent";
import { useAuth } from "@/lib/auth";
import { getTenantRoutes } from "@/lib/pathUtils";
import { cn } from "@/lib/utils";

export function AppShell({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const path = useRouterState({ select: (s) => s.location.pathname });
  const navigate = useNavigate();
  const { session, signOut, orgPath } = useAuth();
  const initials = session?.user.full_name?.slice(0, 1).toUpperCase() ?? "?";

  // Build dynamic nav based on org path
  const routes = useMemo(() => getTenantRoutes(orgPath || ""), [orgPath]);

  const NAV = useMemo(
    () => [
      { to: routes.modelSets, label: "Model Sets", icon: LayoutGrid },
      { to: routes.projects, label: "Projects", icon: FolderKanban },
      { to: routes.brain, label: "Brain", icon: Brain },
      { to: routes.playbooks, label: "My Playbooks", icon: NotebookTabs },
      { to: routes.lessons, label: "Lessons", icon: BookOpen },
      { to: routes.savedDocuments, label: "Saved Documents", icon: FileText },
      { to: routes.savedPrompts, label: "Saved Prompts", icon: MessageSquareText },
      { to: routes.savedVerdicts, label: "Saved Verdicts", icon: Bookmark },
      { to: routes.library, label: "Library", icon: Library },
    ],
    [routes],
  );

  const WORKSPACES = useMemo(
    () => [{ to: routes.chat, label: "Chat Council", icon: MessageSquare }],
    [routes],
  );

  const isScraping = path.startsWith("/scraping");
  const isMaps = path.startsWith("/maps");

  function closeSidebar() {
    setOpen(false);
  }

  return (
    <div className="relative flex min-h-screen w-full text-foreground">
      {isScraping ? (
        <ScrapingDreamSky intensity="calm" className="fixed inset-0 -z-10" />
      ) : (
        <CinematicBackdrop />
      )}

      <header className="fixed top-0 right-0 left-0 z-30 flex h-14 items-center justify-between border-b border-border bg-sidebar/95 px-4 shadow-sm backdrop-blur-md md:hidden">
        <button onClick={() => setOpen(true)} className="p-2 -ml-2">
          <Menu className="size-5" />
        </button>
        <Link
          to={isScraping ? "/scraping" : "/chat"}
          className="flex items-center gap-2 font-display text-lg font-semibold"
        >
          <BrandLogo className="size-6" /> MultiAI
        </Link>
        <div className="w-8" />
      </header>

      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-40 flex h-full min-h-0 w-72 flex-col border-r border-border bg-sidebar/95 shadow-sm backdrop-blur-md transition-transform md:sticky md:top-0 md:h-screen md:translate-x-0",
          open ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <div className="flex h-14 shrink-0 items-center justify-between border-b border-border px-4">
          <Link
            to={isScraping ? "/scraping" : "/chat"}
            onClick={closeSidebar}
            className="flex items-center gap-2 font-display font-semibold"
          >
            <BrandLogo className="size-7" />
            MultiAI
          </Link>
          <button onClick={closeSidebar} className="p-2 md:hidden">
            <X className="size-4" />
          </button>
        </div>

        <nav className="shrink-0 space-y-1 px-3 py-3">
          {WORKSPACES.map((n) => {
            const active =
              n.to === "/scraping"
                ? isScraping
                : n.to === "/maps"
                  ? isMaps
                  : !isScraping && !isMaps;
            return (
              <Link
                key={n.to}
                to={n.to}
                onClick={closeSidebar}
                className={cn(
                  "flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition",
                  active
                    ? "bg-accent font-medium text-foreground"
                    : "text-sidebar-foreground/80 hover:bg-accent",
                )}
              >
                <n.icon className="size-4" /> {n.label}
              </Link>
            );
          })}
        </nav>

        <nav className="shrink-0 space-y-0.5 px-3 pb-1">
          {NAV.map((n) => (
            <Link
              key={n.to}
              to={n.to}
              onClick={closeSidebar}
              className={cn(
                "flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition",
                path.startsWith(n.to)
                  ? "bg-accent font-medium text-foreground"
                  : "text-sidebar-foreground/80 hover:bg-accent",
              )}
            >
              <n.icon className="size-4" /> {n.label}
            </Link>
          ))}
        </nav>

        {isScraping ? (
          <ScrapingSidebarContent onNavigate={closeSidebar} />
        ) : isMaps ? null : (
          <ChatSidebarContent onNavigate={closeSidebar} />
        )}

        <div className="shrink-0 border-t border-border px-3 py-2">
          <Link
            to="/settings"
            onClick={closeSidebar}
            className="flex items-center gap-3 rounded-lg px-3 py-1.5 text-sm hover:bg-accent"
          >
            <Settings className="size-4" /> Settings
          </Link>
          <div className="mt-1 flex items-center gap-2.5 rounded-lg px-2 py-1.5">
            <div className="grid size-8 place-items-center rounded-full bg-primary/15 text-sm font-semibold text-primary">
              {initials}
            </div>
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium">
                {session?.user.full_name ?? "Guest"}
              </div>
              <div className="truncate text-xs text-muted-foreground">{session?.user.email}</div>
            </div>
            <button
              onClick={() => {
                signOut();
                void navigate({ to: "/login" });
              }}
              className="rounded-lg p-2 text-muted-foreground hover:bg-accent hover:text-foreground"
              title="Sign out"
            >
              <LogOut className="size-4" />
            </button>
          </div>
        </div>
      </aside>

      {open && (
        <div onClick={closeSidebar} className="fixed inset-0 z-30 bg-foreground/25 md:hidden" />
      )}

      <main className="relative min-w-0 flex-1 pt-14 md:pt-0">{children}</main>
    </div>
  );
}
