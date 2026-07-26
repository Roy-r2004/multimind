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
import { CouncilGlassSky } from "@/components/chat/CouncilGlassSky";
import { CinematicBackdrop } from "@/components/cinematic/PageChrome";
import { ScrapingDreamSky } from "@/components/scraping/ScrapingDreamSky";
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
  const isChatCouncil = path === "/" || path.startsWith("/chat");
  const isDarkShell = isScraping || isChatCouncil;
  const initials = session?.user.full_name?.slice(0, 1).toUpperCase() ?? "?";

  function closeSidebar() {
    setOpen(false);
  }

  return (
    <div
      className={cn(
        "relative flex min-h-screen w-full",
        isScraping && "text-[#f7f1e4]",
        isChatCouncil && "council-glass text-slate-100",
        !isDarkShell && "text-foreground",
      )}
    >
      {isScraping ? (
        <div className="pointer-events-none fixed inset-0 -z-10 overflow-hidden">
          <ScrapingDreamSky intensity="live" />
        </div>
      ) : isChatCouncil ? (
        <CouncilGlassSky />
      ) : (
        <CinematicBackdrop />
      )}

      <header
        className={cn(
          "fixed top-0 left-0 right-0 z-30 flex h-14 items-center justify-between border-b px-4 shadow-sm md:hidden",
          isScraping
            ? "border-white/10 bg-[#0b161c]/90 text-[#f7f1e4] backdrop-blur-md"
            : isChatCouncil
              ? "border-white/10 bg-[#020617]/85 text-slate-100 backdrop-blur-xl"
              : "border-border bg-sidebar",
        )}
      >
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
          "fixed inset-y-0 left-0 z-40 flex w-72 flex-col border-r shadow-sm transition-transform md:sticky md:top-0 md:h-screen md:translate-x-0",
          open ? "translate-x-0" : "-translate-x-full",
          isScraping
            ? "border-white/10 bg-[#0b161c]/92 text-[#f7f1e4] backdrop-blur-xl"
            : isChatCouncil
              ? "border-white/10 bg-[#020617]/92 text-slate-100 backdrop-blur-xl"
              : "border-border bg-sidebar",
        )}
      >
        <div
          className={cn(
            "flex h-14 items-center justify-between border-b px-4",
            isDarkShell ? "border-white/10" : "border-border",
          )}
        >
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
            const active = n.to === "/scraping" ? isScraping : isChatCouncil;
            return (
              <Link
                key={n.to}
                to={n.to}
                onClick={closeSidebar}
                className={cn(
                  "flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition",
                  isScraping
                    ? active
                      ? "bg-[#d4a84b]/15 font-medium text-[#f3e6c4]"
                      : "text-white/65 hover:bg-white/5 hover:text-[#f7f1e4]"
                    : isChatCouncil
                      ? active
                        ? "bg-gradient-to-r from-sky-500/30 to-violet-500/35 font-medium text-white shadow-[0_0_24px_rgb(99_102_241_/_0.25)]"
                        : "text-slate-300/75 hover:bg-white/5 hover:text-white"
                      : active
                        ? "bg-accent font-medium text-foreground"
                        : "text-sidebar-foreground/80 hover:bg-accent",
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
                "flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition",
                isScraping
                  ? path.startsWith(n.to)
                    ? "bg-white/10 font-medium text-[#f7f1e4]"
                    : "text-white/55 hover:bg-white/5"
                  : isChatCouncil
                    ? path.startsWith(n.to)
                      ? "bg-white/10 font-medium text-white"
                      : "text-slate-400 hover:bg-white/5 hover:text-slate-100"
                    : path.startsWith(n.to)
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
        ) : (
          <ChatSidebarContent onNavigate={closeSidebar} tone={isChatCouncil ? "glass" : "light"} />
        )}

        <div className={cn("border-t p-3", isDarkShell ? "border-white/10" : "border-border")}>
          <Link
            to="/settings"
            onClick={closeSidebar}
            className={cn(
              "flex items-center gap-3 rounded-lg px-3 py-2 text-sm",
              isScraping
                ? "text-white/70 hover:bg-white/5"
                : isChatCouncil
                  ? "text-slate-300 hover:bg-white/5 hover:text-white"
                  : "hover:bg-accent",
            )}
          >
            <Settings className="size-4" /> Settings
          </Link>
          <div className="mt-2 flex items-center gap-3 rounded-lg px-2 py-2">
            <div
              className={cn(
                "grid size-9 place-items-center rounded-full text-sm font-semibold",
                isScraping
                  ? "bg-[#d4a84b]/20 text-[#f3e6c4]"
                  : isChatCouncil
                    ? "bg-gradient-to-br from-sky-400/30 to-violet-500/40 text-white"
                    : "bg-primary/15 text-primary",
              )}
            >
              {initials}
            </div>
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium">
                {session?.user.full_name ?? "Guest"}
              </div>
              <div
                className={cn(
                  "truncate text-xs",
                  isScraping
                    ? "text-white/40"
                    : isChatCouncil
                      ? "text-slate-400"
                      : "text-muted-foreground",
                )}
              >
                {session?.user.email}
              </div>
            </div>
            <button
              onClick={() => {
                signOut();
                void navigate({ to: "/login" });
              }}
              className={cn(
                "rounded-lg p-2",
                isScraping
                  ? "text-white/45 hover:bg-white/5 hover:text-[#f7f1e4]"
                  : isChatCouncil
                    ? "text-slate-400 hover:bg-white/5 hover:text-white"
                    : "text-muted-foreground hover:bg-accent hover:text-foreground",
              )}
              title="Sign out"
            >
              <LogOut className="size-4" />
            </button>
          </div>
        </div>
      </aside>

      {open && (
        <div
          onClick={closeSidebar}
          className={cn(
            "fixed inset-0 z-30 md:hidden",
            isDarkShell ? "bg-black/50" : "bg-foreground/25",
          )}
        />
      )}

      <main className="relative min-w-0 flex-1 pt-14 md:pt-0">{children}</main>
    </div>
  );
}
