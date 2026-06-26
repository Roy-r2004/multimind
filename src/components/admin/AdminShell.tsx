import { Link, useRouterState } from "@tanstack/react-router";
import {
  Activity,
  Brain,
  Building2,
  FileText,
  FolderKanban,
  LayoutDashboard,
  LogOut,
  MessageSquare,
  ScrollText,
  Shield,
  Users,
  UserCog,
  BarChart3,
} from "lucide-react";
import { BrandLogo } from "@/components/BrandLogo";
import { CinematicBackdrop } from "@/components/cinematic/PageChrome";

const NAV = [
  { to: "/admin", label: "Dashboard", icon: LayoutDashboard, exact: true },
  { to: "/admin/audit", label: "Audit Logs", icon: ScrollText },
  { to: "/admin/users", label: "Users", icon: Users },
  { to: "/admin/chats", label: "Chats", icon: MessageSquare },
  { to: "/admin/brains", label: "User Brains", icon: Brain },
  { to: "/admin/lessons", label: "Lessons", icon: FileText },
  { to: "/admin/members", label: "Members", icon: UserCog },
  { to: "/admin/projects", label: "Projects", icon: FolderKanban },
  { to: "/admin/usage", label: "Usage & Billing", icon: BarChart3 },
  { to: "/admin/security", label: "Security", icon: Shield },
  { to: "/admin/organization", label: "Organization", icon: Building2 },
] as const;

export function AdminShell({
  orgName,
  userName,
  onSignOut,
  children,
}: {
  orgName?: string;
  userName?: string;
  onSignOut?: () => void;
  children?: React.ReactNode;
}) {
  const pathname = useRouterState({ select: (s) => s.location.pathname });

  return (
    <div className="relative flex min-h-screen text-foreground">
      <CinematicBackdrop />
      <aside className="relative z-20 flex w-64 shrink-0 flex-col border-r border-border bg-sidebar/95 shadow-sm backdrop-blur">
        <div className="flex h-14 items-center gap-2 border-b border-border px-4">
          <BrandLogo className="size-7" />
          <div>
            <div className="font-display text-sm font-semibold leading-tight">MultiAI Admin</div>
            {orgName && (
              <div className="truncate text-[11px] text-muted-foreground">{orgName}</div>
            )}
          </div>
        </div>
        <nav className="flex-1 space-y-0.5 overflow-y-auto p-3">
          {NAV.map(({ to, label, icon: Icon, ...rest }) => {
            const exact = "exact" in rest && rest.exact;
            const active = exact ? pathname === to : pathname === to || pathname.startsWith(`${to}/`);
            return (
              <Link
                key={to}
                to={to}
                className={[
                  "flex items-center gap-2.5 rounded-xl px-3 py-2 text-sm transition-colors",
                  active
                    ? "bg-primary/10 font-medium text-primary"
                    : "text-muted-foreground hover:bg-accent hover:text-foreground",
                ].join(" ")}
              >
                <Icon className="size-4 shrink-0" />
                {label}
              </Link>
            );
          })}
        </nav>
        <div className="border-t border-border p-3">
          {userName && (
            <div className="mb-2 truncate px-2 text-xs text-muted-foreground">{userName}</div>
          )}
          {onSignOut && (
            <button
              type="button"
              onClick={onSignOut}
              className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-sm text-muted-foreground hover:bg-accent hover:text-foreground"
            >
              <LogOut className="size-4" /> Sign out
            </button>
          )}
        </div>
      </aside>
      <div className="relative z-10 flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 items-center border-b border-border bg-card/70 px-6 backdrop-blur">
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Activity className="size-4 text-primary" />
            Enterprise control plane — full org visibility & audit trail
          </div>
        </header>
        <main className="flex-1 overflow-auto">{children}</main>
      </div>
    </div>
  );
}
