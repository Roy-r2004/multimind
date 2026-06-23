import { createFileRoute, Link } from "@tanstack/react-router";
import { Sparkles } from "lucide-react";

export const Route = createFileRoute("/login")({
  head: () => ({ meta: [{ title: "Log in — MultiAI" }] }),
  component: LoginPage,
});

function LoginPage() {
  return (
    <AuthShell title="Welcome back" subtitle="Log in to continue your conversations.">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          window.location.href = "/chat";
        }}
        className="space-y-4"
      >
        <Field label="Email">
          <input type="email" required defaultValue="sara@acme.co" className="input" />
        </Field>
        <Field
          label="Password"
          right={
            <a href="#" className="text-xs text-primary hover:underline">
              Forgot?
            </a>
          }
        >
          <input type="password" required defaultValue="••••••••" className="input" />
        </Field>
        <button className="btn-primary w-full">Log in</button>
        <button type="button" className="btn-outline w-full">
          Continue with Google
        </button>
        <p className="text-center text-sm text-muted-foreground">
          No account?{" "}
          <Link to="/signup" className="text-primary hover:underline">
            Sign up
          </Link>
        </p>
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
    <div className="grid min-h-screen md:grid-cols-2">
      <div className="hidden md:flex flex-col justify-between bg-[radial-gradient(80%_60%_at_20%_0%,oklch(0.85_0.08_200)_0%,oklch(0.92_0.04_80)_60%,transparent_100%)] p-10">
        <Link to="/" className="flex items-center gap-2 font-display text-lg font-semibold">
          <span className="grid size-8 place-items-center rounded-xl bg-primary text-primary-foreground">
            <Sparkles className="size-4" />
          </span>
          MultiAI
        </Link>
        <blockquote className="max-w-md text-2xl font-display leading-tight">
          “I stopped switching tabs between three chatbots. MultiAI just shows me the answer.”
          <footer className="mt-3 text-sm font-sans text-muted-foreground">
            — Liam, product designer
          </footer>
        </blockquote>
        <div className="text-xs text-muted-foreground">© 2026 MultiAI prototype</div>
      </div>
      <div className="flex items-center justify-center p-8">
        <div className="w-full max-w-sm">
          <div className="md:hidden mb-8">
            <Link to="/" className="flex items-center gap-2 font-display text-lg font-semibold">
              <span className="grid size-8 place-items-center rounded-xl bg-primary text-primary-foreground">
                <Sparkles className="size-4" />
              </span>
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
      .input { width: 100%; border: 1px solid var(--color-input); background: var(--color-card); border-radius: 0.625rem; padding: 0.55rem 0.75rem; font-size: 0.9rem; outline: none; }
      .input:focus { box-shadow: 0 0 0 3px color-mix(in oklab, var(--color-ring) 30%, transparent); border-color: var(--color-ring); }
      .btn-primary { background: var(--color-primary); color: var(--color-primary-foreground); border-radius: 0.625rem; padding: 0.6rem 0.9rem; font-size: 0.9rem; font-weight: 500; cursor: pointer; }
      .btn-primary:hover { opacity: 0.92; }
      .btn-outline { border: 1px solid var(--color-border); background: var(--color-card); border-radius: 0.625rem; padding: 0.55rem 0.9rem; font-size: 0.9rem; font-weight: 500; cursor: pointer; }
      .btn-outline:hover { background: var(--color-accent); }
    `}</style>
  );
}
