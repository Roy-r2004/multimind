import { createFileRoute, Link, useNavigate, useRouterState } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { Eye, EyeOff, Lock, Mail, ShieldCheck } from "lucide-react";
import { BrandLogo } from "@/components/BrandLogo";
import { CinematicBackdrop, GlassCard } from "@/components/cinematic/PageChrome";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

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
  const [showPassword, setShowPassword] = useState(false);
  const isAdminRedirect = redirect === "/admin";

  useEffect(() => {
    if (!isAdminRedirect) setError(null);
  }, [isAdminRedirect]);

  useEffect(() => {
    void api.auth.warm().catch(() => undefined);
  }, []);

  return (
    <div className="relative min-h-screen overflow-hidden text-foreground">
      <CinematicBackdrop />
      <div className="relative mx-auto flex min-h-screen max-w-6xl flex-col px-4 py-10 md:px-8">
        <div className="elevate-hero mb-8 text-center">
          <p className="text-[11px] font-semibold uppercase tracking-[0.32em] text-primary">
            02 — Login
          </p>
          <h1 className="mt-3 font-display text-4xl tracking-tight md:text-5xl">
            Seamless access.{" "}
            <span className="text-gradient italic">Infinite insights.</span>
          </h1>
          <p className="mt-4 flex items-center justify-center gap-3 text-sm text-muted-foreground">
            <span className="h-px w-10 bg-border" />
            Secure sign-in. Your workspace awaits.
            <span className="h-px w-10 bg-border" />
          </p>
        </div>

        <GlassCard glow className="mx-auto grid w-full max-w-5xl flex-1 overflow-hidden rounded-[2rem] md:grid-cols-2">
          <div className="relative flex flex-col justify-between border-b border-border bg-gradient-to-br from-sky-50/80 via-card to-blue-50/60 p-8 md:border-r md:border-b-0 md:p-10">
            <Link to="/" className="flex items-center gap-2 font-display text-lg font-semibold">
              <BrandLogo className="size-9" />
              MultiAI
            </Link>
            <div className="my-10 md:my-0">
              <h2 className="font-display text-3xl leading-tight md:text-4xl">
                Ask once.
                <br />
                Compare frontier models.
                <br />
                <span className="text-gradient italic">One verdict.</span>
              </h2>
              <p className="mt-4 text-sm text-muted-foreground">
                GPT-4.1 · Claude Sonnet 4 · Gemini 2.5 Pro · Grok · DeepSeek V3
              </p>
            </div>
            <div>
              <div className="inline-flex items-center gap-3 rounded-2xl border border-border bg-card/80 px-3 py-2.5 text-left shadow-sm">
                <span className="grid size-9 place-items-center rounded-full bg-primary/10 text-primary">
                  <ShieldCheck className="size-4" />
                </span>
                <div>
                  <div className="text-sm font-medium">Enterprise-grade security</div>
                  <div className="text-xs text-muted-foreground">
                    Your data is encrypted and never shared.
                  </div>
                </div>
              </div>
              <p className="mt-6 text-xs text-muted-foreground">© 2026 MultiAI</p>
            </div>
          </div>

          <div className="flex flex-col justify-center p-8 md:p-10">
            <h3 className="text-2xl font-semibold">Welcome back</h3>
            <p className="mt-2 text-sm text-muted-foreground">
              {isAdminRedirect
                ? "Log in with an organization owner or admin account."
                : "Log in to continue your conversations."}
            </p>

            <form
              onChange={() => setError(null)}
              onSubmit={async (e) => {
                e.preventDefault();
                setError(null);
                setLoading(true);
                const fd = new FormData(e.currentTarget);
                try {
                  const session = await signIn(
                    String(fd.get("email")),
                    String(fd.get("password")),
                  );
                  const isAdmin =
                    session.organization.role === "owner" ||
                    session.organization.role === "admin";
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
              className="mt-8 space-y-4"
            >
              {error && (
                <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                  {error}
                </div>
              )}
              <label className="block">
                <span className="mb-1.5 block text-sm font-medium">Email</span>
                <div className="relative">
                  <Mail className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
                  <input
                    name="email"
                    type="email"
                    required
                    defaultValue={isAdminRedirect ? "admin@gmail.com" : "chafic@gmail.com"}
                    className="w-full rounded-xl border border-border bg-card py-2.5 pr-3 pl-10 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
                  />
                </div>
              </label>
              <label className="block">
                <span className="mb-1.5 block text-sm font-medium">Password</span>
                <div className="relative">
                  <Lock className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
                  <input
                    name="password"
                    type={showPassword ? "text" : "password"}
                    required
                    defaultValue="password123"
                    className="w-full rounded-xl border border-border bg-card py-2.5 pr-10 pl-10 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword((v) => !v)}
                    className="absolute top-1/2 right-3 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                    aria-label={showPassword ? "Hide password" : "Show password"}
                  >
                    {showPassword ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
                  </button>
                </div>
              </label>
              <div className="flex items-center justify-between text-sm">
                <label className="inline-flex items-center gap-2 text-muted-foreground">
                  <input type="checkbox" name="remember" className="size-4 rounded border-border" />
                  Remember me
                </label>
                <span className="text-muted-foreground/70">Forgot password?</span>
              </div>
              <button
                type="submit"
                disabled={loading}
                className={cn(
                  "w-full rounded-xl bg-primary py-3 text-sm font-semibold text-primary-foreground shadow-sm hover:bg-primary/90 disabled:opacity-50",
                )}
              >
                {loading ? "Signing in…" : "Log in"}
              </button>
            </form>
            <p className="mt-6 text-center text-sm text-muted-foreground">
              New to MultiAI? Ask your workspace admin for an invite.
            </p>
          </div>
        </GlassCard>
      </div>
    </div>
  );
}

/** Kept for any pages that still import AuthShell / Field helpers */
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
    <div className="relative min-h-screen text-foreground">
      <CinematicBackdrop />
      <div className="relative mx-auto flex min-h-screen max-w-md items-center px-4 py-10">
        <GlassCard glow className="w-full rounded-[2rem] p-8">
          <Link to="/" className="flex items-center gap-2 font-display font-semibold">
            <BrandLogo className="size-8" /> MultiAI
          </Link>
          <h1 className="mt-6 text-2xl font-semibold">{title}</h1>
          <p className="mt-2 text-sm text-muted-foreground">{subtitle}</p>
          <div className="mt-6">{children}</div>
        </GlassCard>
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
      .btn-primary { background: var(--color-primary); color: var(--color-primary-foreground); border-radius: 0.75rem; padding: 0.6rem 0.9rem; font-size: 0.9rem; font-weight: 500; cursor: pointer; }
      .btn-primary:hover { opacity: 0.92; }
      .btn-outline { border: 1px solid var(--color-border); background: var(--color-card); border-radius: 0.625rem; padding: 0.55rem 0.9rem; font-size: 0.9rem; font-weight: 500; cursor: pointer; }
    `}</style>
  );
}
