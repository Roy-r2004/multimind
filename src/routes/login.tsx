import { createFileRoute, Link, useNavigate, useRouterState } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { BrandLogo } from "@/components/BrandLogo";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export const Route = createFileRoute("/login")({
  head: () => ({ meta: [{ title: "Log in — MultiAI" }] }),
  component: LoginPage,
});

function LoginPage() {
  const { signIn, signOut } = useAuth();
  const navigate = useNavigate();
  const redirect = useRouterState({
    select: (state) => {
      const search = state.location.search as Record<string, unknown>;
      const value = search.redirect;
      return typeof value === "string" ? value : undefined;
    },
  });
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const isAdminRedirect = redirect === "/admin";

  useEffect(() => {
    if (!isAdminRedirect) {
      setError(null);
    }
  }, [isAdminRedirect]);

  // Ping the API as soon as login loads so a slept Render free instance wakes up
  // before the user finishes typing / clicking submit.
  useEffect(() => {
    void api.auth.warm().catch(() => undefined);
  }, []);

  return (
    <AuthShell
      title="Welcome back"
      subtitle={
        isAdminRedirect
          ? "Log in with an organization owner or admin account."
          : "Log in to continue your conversations."
      }
    >
      <form
        onChange={() => setError(null)}
        onSubmit={async (e) => {
          e.preventDefault();
          setError(null);
          setLoading(true);
          const fd = new FormData(e.currentTarget);
          try {
            const session = await signIn(String(fd.get("email")), String(fd.get("password")));
            const isAdmin =
              session.organization.role === "owner" || session.organization.role === "admin";
            if (isAdminRedirect) {
              if (isAdmin) {
                void navigate({ to: "/admin" });
                return;
              }
              signOut();
              setError("Access denied. Admin access requires an owner or admin account.");
              return;
            }
            if (isAdmin) {
              void navigate({ to: "/admin" });
              return;
            }
            void navigate({ to: "/chat" });
          } catch (err) {
            setError(err instanceof Error ? err.message : "Login failed");
          } finally {
            setLoading(false);
          }
        }}
        className="space-y-4"
      >
        {error && (
          <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {error}
          </div>
        )}
        <Field label="Email">
          <input
            name="email"
            type="email"
            required
            defaultValue={isAdminRedirect ? "admin@gmail.com" : "chafic@gmail.com"}
            className="input"
          />
        </Field>
        <Field label="Password">
          <input
            name="password"
            type="password"
            required
            defaultValue="password123"
            className="input"
          />
        </Field>
        <button className="btn-primary w-full" disabled={loading}>
          {loading ? "Signing in…" : "Log in"}
        </button>
      </form>
    </AuthShell>
  );
}

export function AuthShell({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle: string;
  children: React.ReactNode;
}) {
  return (
    <div className="relative grid min-h-screen bg-background md:grid-cols-2">
      <div className="grain pointer-events-none absolute inset-0 opacity-20" />
      <div className="relative hidden flex-col justify-between border-r border-border bg-gradient-to-br from-sky-50 via-white to-blue-50 p-10 md:flex">
        <Link to="/" className="flex items-center gap-2 font-display text-lg font-semibold">
          <BrandLogo className="size-8" />
          MultiAI
        </Link>
        <blockquote className="max-w-md text-2xl font-display leading-tight text-foreground/90">
          Ask once. Compare frontier models. One verdict.
          <footer className="mt-3 text-sm font-sans text-muted-foreground">
            GPT-4.1 · Claude Sonnet 4 · Gemini 2.5 Pro · Grok · DeepSeek V3
          </footer>
        </blockquote>
        <div className="text-xs text-muted-foreground">© 2026 MultiAI</div>
      </div>
      <div className="relative flex items-center justify-center p-8">
        <div className="w-full max-w-sm">
          <div className="md:hidden mb-8">
            <Link to="/" className="flex items-center gap-2 font-display text-lg font-semibold">
              <BrandLogo className="size-8" />
              MultiAI
            </Link>
          </div>
          <h1 className="text-2xl font-semibold">{title}</h1>
          <p className="mt-2 text-sm text-muted-foreground">{subtitle}</p>
          <div className="mt-6">{children}</div>
        </div>
      </div>
      <FieldStyles />
    </div>
  );
}

export function Field({
  label,
  children,
  right,
}: {
  label: string;
  children: React.ReactNode;
  right?: React.ReactNode;
}) {
  return (
    <label className="block">
      <div className="mb-1.5 flex items-center justify-between text-sm font-medium">
        {label}
        {right}
      </div>
      {children}
    </label>
  );
}

export function FieldStyles() {
  return (
    <style>{`
      .input { width: 100%; border: 1px solid var(--color-border); background: var(--color-card); border-radius: 0.75rem; padding: 0.55rem 0.75rem; font-size: 0.9rem; outline: none; }
      .input:focus { box-shadow: 0 0 0 3px color-mix(in oklab, var(--color-primary) 20%, transparent); border-color: var(--color-primary); }
      .btn-primary { background: var(--color-primary); color: var(--color-primary-foreground); border-radius: 0.75rem; padding: 0.6rem 0.9rem; font-size: 0.9rem; font-weight: 500; cursor: pointer; box-shadow: 0 1px 2px color-mix(in oklab, var(--color-primary) 25%, transparent); }
      .btn-primary:hover { opacity: 0.92; }
      .btn-outline { border: 1px solid var(--color-border); background: var(--color-card); border-radius: 0.625rem; padding: 0.55rem 0.9rem; font-size: 0.9rem; font-weight: 500; cursor: pointer; }
      .btn-outline:hover { background: var(--color-accent); }
    `}</style>
  );
}
