import { createFileRoute, Link, useNavigate, useRouterState } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { Eye, EyeOff, Lock, Mail, ShieldCheck } from "lucide-react";
import { BrandLogo } from "@/components/BrandLogo";
import { CouncilGlassSky } from "@/components/chat/CouncilGlassSky";
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
    <div className="council-glass relative min-h-screen overflow-hidden text-slate-100">
      <CouncilGlassSky />
      <div className="relative mx-auto flex min-h-screen max-w-6xl flex-col px-4 py-10 md:px-8">
        <div className="mb-8 text-center">
          <p className="text-[11px] font-semibold uppercase tracking-[0.32em] text-sky-300/90">
            02 — Login
          </p>
          <h1 className="mt-3 font-display text-4xl tracking-tight text-white md:text-6xl">
            Seamless access.{" "}
            <span className="text-gradient italic">Infinite insights.</span>
          </h1>
          <p className="mt-4 flex items-center justify-center gap-3 text-sm text-slate-300/80">
            <span className="h-px w-10 bg-white/20" />
            Secure sign-in. Your workspace awaits.
            <span className="h-px w-10 bg-white/20" />
          </p>
        </div>

        <div className="council-glass-panel mx-auto grid w-full max-w-5xl flex-1 overflow-hidden rounded-[2rem] md:grid-cols-2">
          <div className="relative flex flex-col justify-between border-b border-white/10 p-8 md:border-b-0 md:border-r md:p-10">
            <Link to="/" className="flex items-center gap-2 font-sans text-lg font-semibold">
              <BrandLogo className="size-9" />
              MultiAI
            </Link>
            <div className="my-10 md:my-0">
              <h2 className="font-display text-3xl leading-tight text-white md:text-4xl">
                Ask once.
                <br />
                Compare frontier models.
                <br />
                <span className="text-gradient italic">One verdict.</span>
              </h2>
              <p className="mt-4 text-sm text-slate-300/80">
                GPT-4.1 · Claude Sonnet 4 · Gemini 2.5 Pro · Grok · DeepSeek V3
              </p>
            </div>
            <div>
              <div className="inline-flex items-center gap-3 rounded-2xl border border-white/15 bg-white/5 px-3 py-2.5 text-left backdrop-blur">
                <span className="grid size-9 place-items-center rounded-full bg-sky-400/20 text-sky-200">
                  <ShieldCheck className="size-4" />
                </span>
                <div>
                  <div className="text-sm font-medium text-white">Enterprise-grade security</div>
                  <div className="text-xs text-slate-400">
                    Your data is encrypted and never shared.
                  </div>
                </div>
              </div>
              <p className="mt-6 text-xs text-slate-500">© 2026 MultiAI</p>
            </div>
          </div>

          <div className="flex flex-col justify-center p-8 md:p-10">
            <h3 className="font-sans text-2xl font-semibold text-white">Welcome back</h3>
            <p className="mt-2 text-sm text-slate-400">
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
                <div className="rounded-lg border border-rose-400/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
                  {error}
                </div>
              )}
              <label className="block">
                <span className="mb-1.5 block text-sm font-medium text-slate-200">Email</span>
                <div className="relative">
                  <Mail className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-slate-400" />
                  <input
                    name="email"
                    type="email"
                    required
                    defaultValue={isAdminRedirect ? "admin@gmail.com" : "chafic@gmail.com"}
                    className="w-full rounded-xl border border-white/20 bg-white/5 py-2.5 pr-3 pl-10 text-sm text-white outline-none placeholder:text-slate-500 focus:border-sky-300/50 focus:ring-2 focus:ring-sky-400/20"
                  />
                </div>
              </label>
              <label className="block">
                <span className="mb-1.5 block text-sm font-medium text-slate-200">Password</span>
                <div className="relative">
                  <Lock className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-slate-400" />
                  <input
                    name="password"
                    type={showPassword ? "text" : "password"}
                    required
                    defaultValue="password123"
                    className="w-full rounded-xl border border-white/20 bg-white/5 py-2.5 pr-10 pl-10 text-sm text-white outline-none placeholder:text-slate-500 focus:border-sky-300/50 focus:ring-2 focus:ring-sky-400/20"
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword((v) => !v)}
                    className="absolute top-1/2 right-3 -translate-y-1/2 text-slate-400 hover:text-white"
                    aria-label={showPassword ? "Hide password" : "Show password"}
                  >
                    {showPassword ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
                  </button>
                </div>
              </label>
              <div className="flex items-center justify-between text-sm">
                <label className="inline-flex items-center gap-2 text-slate-300">
                  <input
                    type="checkbox"
                    name="remember"
                    className="size-4 rounded border-white/30 bg-transparent text-sky-400"
                  />
                  Remember me
                </label>
                <span className="text-slate-500">Forgot password?</span>
              </div>
              <button
                type="submit"
                disabled={loading}
                className={cn(
                  "council-glass-cta w-full rounded-xl py-3 text-sm font-semibold disabled:opacity-50",
                )}
              >
                {loading ? "Signing in…" : "Log in"}
              </button>
            </form>
            <p className="mt-6 text-center text-sm text-slate-400">
              New to MultiAI? Ask your workspace admin for an invite.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

/** Kept for signup page compatibility */
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
    <div className="council-glass relative min-h-screen text-slate-100">
      <CouncilGlassSky />
      <div className="relative mx-auto flex min-h-screen max-w-md items-center px-4 py-10">
        <div className="council-glass-panel w-full rounded-[2rem] p-8">
          <Link to="/" className="flex items-center gap-2 font-semibold">
            <BrandLogo className="size-8" /> MultiAI
          </Link>
          <h1 className="mt-6 text-2xl font-semibold text-white">{title}</h1>
          <p className="mt-2 text-sm text-slate-400">{subtitle}</p>
          <div className="mt-6">{children}</div>
        </div>
      </div>
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
      <div className="mb-1.5 flex items-center justify-between text-sm font-medium text-slate-200">
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
      .input { width: 100%; border: 1px solid rgb(255 255 255 / 0.2); background: rgb(255 255 255 / 0.05); border-radius: 0.75rem; padding: 0.55rem 0.75rem; font-size: 0.9rem; outline: none; color: white; }
      .input:focus { box-shadow: 0 0 0 3px rgb(56 189 248 / 0.2); border-color: rgb(125 211 252 / 0.5); }
      .btn-primary { background: linear-gradient(135deg, #38bdf8, #6366f1 55%, #a855f7); color: white; border-radius: 0.75rem; padding: 0.6rem 0.9rem; font-size: 0.9rem; font-weight: 600; cursor: pointer; }
      .btn-primary:hover { filter: brightness(1.06); }
      .btn-outline { border: 1px solid rgb(255 255 255 / 0.2); background: rgb(255 255 255 / 0.05); border-radius: 0.625rem; padding: 0.55rem 0.9rem; font-size: 0.9rem; font-weight: 500; cursor: pointer; color: white; }
    `}</style>
  );
}
