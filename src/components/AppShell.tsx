import { Link, useNavigate, useRouterState } from "@tanstack/react-router";
import { useState, type ReactNode } from "react";
import {
  BookOpen,
  Brain,
  FolderKanban,
  LayoutGrid,
  LogOut,
  Bookmark,
  FileText,
  Menu,
  MessageSquare,
  Search,
  Settings,
  X,
} from "lucide-react";
import { BrandLogo } from "@/components/BrandLogo";
import { CinematicBackdrop } from "@/components/cinematic/PageChrome";
import { ChatSidebarContent } from "@/components/sidebar/ChatSidebarContent";
import { ScrapingSidebarContent } from "@/components/sidebar/ScrapingSidebarContent";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

const NAV = [
  { to: "/model-sets", label: "Model Sets", icon: LayoutGrid },
  { to: "/projects", label: "Projects", icon: FolderKanban },
  { to: "/brain", label: "Brain", icon: Brain },
  { to: "/lessons", label: "Lessons", icon: BookOpen },
  { to: "/saved-documents", label: "Saved Documents", icon: FileText },
  { to: "/saved-verdicts", label: "Saved Verdicts", icon: Bookmark },
];

const WORKSPACES = [
  { to: "/chat", label: "Chat Council", icon: MessageSquare },
  { to: "/scraping", label: "Scraping Council", icon: Search },
];

export function AppShell({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const path = useRouterState({ select: (s) => s.location.pathname });
  const navigate = useNavigate();
  const { session, signOut } = useAuth();
  const isScraping = path.startsWith("/scraping");
  const initials = session?.user.full_name?.slice(0, 1).toUpperCase() ?? "?";

  function closeSidebar() {
    setOpen(false);
  }

  return (
    <div className="relative flex min-h-screen w-full text-foreground">
      <CinematicBackdrop />

      <header className="fixed top-0 left-0 right-0 z-30 flex h-14 items-center justify-between border-b border-border bg-sidebar px-4 shadow-sm md:hidden">
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
          "fixed inset-y-0 left-0 z-40 flex w-72 flex-col border-r border-border bg-sidebar shadow-sm transition-transform md:sticky md:top-0 md:h-screen md:translate-x-0",
          open ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <div className="flex h-14 items-center justify-between border-b border-border px-4">
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

        <nav className="space-y-1 px-3 py-3">
          {WORKSPACES.map((n) => {
            const active = n.to === "/scraping" ? isScraping : !isScraping;
            return (
              <Link
                key={n.to}
                to={n.to}
                onClick={closeSidebar}
                className={cn(
                  "flex items-center gap-3 rounded-lg px-3 py-2 text-sm text-sidebar-foreground/80 transition hover:bg-accent",
                  active && "bg-accent font-medium text-foreground",
                )}
              >
                <n.icon className="size-4" /> {n.label}
              </Link>
            );
          })}
        </nav>

        <nav className="space-y-0.5 px-3">
          {NAV.map((n) => (
            <Link
              key={n.to}
              to={n.to}
              onClick={closeSidebar}
              className={cn(
                "flex items-center gap-3 rounded-lg px-3 py-2 text-sm text-sidebar-foreground/80 transition hover:bg-accent",
                path.startsWith(n.to) && "bg-accent font-medium text-foreground",
              )}
            >
              <n.icon className="size-4" /> {n.label}
            </Link>
          ))}
        </nav>

        {isScraping ? (
          <ScrapingSidebarContent onNavigate={closeSidebar} />
        ) : (
          <ChatSidebarContent onNavigate={closeSidebar} />
        )}

        <div className="border-t border-border p-3">
          <Link
            to="/settings"
            onClick={closeSidebar}
            className="flex items-center gap-3 rounded-lg px-3 py-2 text-sm hover:bg-accent"
          >
            <Settings className="size-4" /> Settings
          </Link>
          <div className="mt-2 flex items-center gap-3 rounded-lg px-2 py-2">
            <div className="grid size-9 place-items-center rounded-full bg-primary/15 text-sm font-semibold text-primary">
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

      <main className="relative flex-1 min-w-0 pt-14 md:pt-0">{children}</main>
    </div>
  );
}
