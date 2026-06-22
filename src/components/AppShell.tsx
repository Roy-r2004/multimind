import { Link, useRouterState } from "@tanstack/react-router";
import { useState, type ReactNode } from "react";
import {
  MessageSquarePlus,
  Search,
  Settings,
  FolderKanban,
  LayoutGrid,
  FileText,
  Sparkles,
  Menu,
  X,
  History,
} from "lucide-react";
import { SAMPLE_CHATS } from "@/lib/mock";
import { cn } from "@/lib/utils";

const NAV = [
  { to: "/projects", label: "Projects", icon: FolderKanban },
  { to: "/model-sets", label: "Model Sets", icon: LayoutGrid },
  { to: "/templates", label: "Templates", icon: FileText },
];

export function AppShell({ children, rightPanel }: { children: ReactNode; rightPanel?: ReactNode }) {
  const [open, setOpen] = useState(false);
  const path = useRouterState({ select: (s) => s.location.pathname });

  return (
    <div className="flex min-h-screen w-full bg-background text-foreground">
      {/* Mobile top bar */}
      <header className="fixed top-0 left-0 right-0 z-30 flex h-14 items-center justify-between border-b border-border bg-sidebar/95 px-4 backdrop-blur md:hidden">
        <button onClick={() => setOpen(true)} className="p-2 -ml-2 cursor-pointer">
          <Menu className="size-5" />
        </button>
        <Link to="/" className="flex items-center gap-2 font-display font-semibold">
          <Sparkles className="size-4 text-primary" /> MultiAI
        </Link>
        <div className="w-8" />
      </header>

      {/* Sidebar */}
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-40 flex w-72 flex-col border-r border-sidebar-border bg-sidebar transition-transform md:sticky md:top-0 md:h-screen md:translate-x-0",
          open ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <div className="flex h-14 items-center justify-between px-4 border-b border-sidebar-border">
          <Link to="/" onClick={() => setOpen(false)} className="flex items-center gap-2 font-display font-semibold">
            <span className="grid size-7 place-items-center rounded-lg bg-primary text-primary-foreground"><Sparkles className="size-4" /></span>
            MultiAI
          </Link>
          <button onClick={() => setOpen(false)} className="p-2 md:hidden cursor-pointer"><X className="size-4" /></button>
        </div>

        <div className="px-3 pt-3">
          <Link
            to="/"
            onClick={() => setOpen(false)}
            className="flex w-full items-center gap-2 rounded-xl bg-primary px-3 py-2.5 text-sm font-medium text-primary-foreground shadow-sm hover:opacity-90"
          >
            <MessageSquarePlus className="size-4" /> New chat
          </Link>
          <div className="relative mt-3">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
            <input
              placeholder="Search chats…"
              className="w-full rounded-lg border border-sidebar-border bg-background py-2 pl-9 pr-3 text-sm outline-none focus:ring-2 focus:ring-ring/40"
            />
          </div>
        </div>

        <div className="px-3 mt-4">
          <div className="flex items-center gap-2 px-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">
            <History className="size-3.5" /> Recent
          </div>
          <div className="mt-1 max-h-[35vh] overflow-y-auto space-y-0.5">
            {SAMPLE_CHATS.map((c) => (
              <Link
                key={c.id}
                to="/"
                onClick={() => setOpen(false)}
                className="block truncate rounded-lg px-3 py-2 text-sm text-sidebar-foreground/80 hover:bg-accent/60"
                title={c.title}
              >
                {c.title}
              </Link>
            ))}
          </div>
        </div>

        <nav className="px-3 pt-4 space-y-0.5">
          {NAV.map((n) => {
            const active = path.startsWith(n.to);
            return (
              <Link
                key={n.to}
                to={n.to}
                onClick={() => setOpen(false)}
                className={cn(
                  "flex items-center gap-3 rounded-lg px-3 py-2 text-sm text-sidebar-foreground/80 hover:bg-accent/60",
                  active && "bg-accent text-accent-foreground font-medium",
                )}
              >
                <n.icon className="size-4" /> {n.label}
              </Link>
            );
          })}
        </nav>

        <div className="mt-auto border-t border-sidebar-border p-3">
          <Link
            to="/settings"
            onClick={() => setOpen(false)}
            className="flex items-center gap-3 rounded-lg px-3 py-2 text-sm hover:bg-accent/60"
          >
            <Settings className="size-4" /> Settings
          </Link>
          <Link to="/settings" onClick={() => setOpen(false)} className="mt-2 flex items-center gap-3 rounded-lg px-2 py-2 hover:bg-accent/60">
            <div className="grid size-8 place-items-center rounded-full bg-accent text-accent-foreground text-sm font-semibold">S</div>
            <div className="min-w-0">
              <div className="truncate text-sm font-medium">Sara K.</div>
              <div className="truncate text-xs text-muted-foreground">Pro plan</div>
            </div>
          </Link>
        </div>
      </aside>

      {/* backdrop */}
      {open && <div onClick={() => setOpen(false)} className="fixed inset-0 z-30 bg-foreground/30 md:hidden" />}

      <main className="flex-1 min-w-0 pt-14 md:pt-0">
        <div className="flex">
          <div className="flex-1 min-w-0">{children}</div>
          {rightPanel && <div className="hidden xl:block w-80 border-l border-border bg-sidebar/40">{rightPanel}</div>}
        </div>
      </main>
    </div>
  );
}
