import { Link, useNavigate } from "@tanstack/react-router";
import { useEffect } from "react";
import { Loader2, ShieldAlert } from "lucide-react";
import { AdminShell } from "@/components/admin/AdminShell";
import { GlassCard } from "@/components/cinematic/PageChrome";
import { useAuth } from "@/lib/auth";

const ADMIN_ROLES = new Set(["owner", "admin"]);

export function AdminGuard({ children }: { children: React.ReactNode }) {
  const { session, isLoading, signOut } = useAuth();
  const navigate = useNavigate();
  const role = session?.organization.role ?? "";
  const canViewAdmin = ADMIN_ROLES.has(role);

  useEffect(() => {
    if (!isLoading && !session) {
      void navigate({ to: "/login", search: { redirect: "/admin" } });
    }
  }, [isLoading, navigate, session]);

  function signOutToLogin() {
    signOut();
    if (typeof window !== "undefined") {
      window.location.replace("/login?redirect=%2Fadmin");
      return;
    }
    void navigate({ to: "/login", search: { redirect: "/admin" } });
  }

  if (isLoading) {
    return (
      <AdminShell>
        <div className="flex justify-center py-24">
          <Loader2 className="size-6 animate-spin text-muted-foreground" />
        </div>
      </AdminShell>
    );
  }

  if (!session) {
    return (
      <AdminShell>
        <div className="flex justify-center py-24">
          <Loader2 className="size-6 animate-spin text-muted-foreground" />
        </div>
      </AdminShell>
    );
  }

  if (!canViewAdmin) {
    return (
      <AdminShell onSignOut={signOutToLogin}>
        <div className="mx-auto max-w-lg px-6 py-20">
          <GlassCard className="p-10 text-center">
            <ShieldAlert className="mx-auto size-8 text-destructive" />
            <h1 className="mt-4 text-xl font-semibold">Access denied</h1>
            <p className="mt-2 text-sm text-muted-foreground">
              Admin dashboards are available only to organization owners and admins.
            </p>
            <Link
              to="/login"
              search={{ redirect: "/admin" }}
              className="mt-6 inline-flex rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
            >
              Log in as admin
            </Link>
          </GlassCard>
        </div>
      </AdminShell>
    );
  }

  return (
    <AdminShell
      orgName={session.organization.name}
      userName={session.user.full_name}
      onSignOut={signOutToLogin}
    >
      {children}
    </AdminShell>
  );
}
