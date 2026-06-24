import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { AuthShell, Field } from "./login";
import { useAuth } from "@/lib/auth";

export const Route = createFileRoute("/signup")({
  head: () => ({ meta: [{ title: "Sign up — MultiAI" }] }),
  component: SignupPage,
});

function SignupPage() {
  const { signUp } = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  return (
    <AuthShell title="Create your account" subtitle="Start comparing AI answers in under a minute.">
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setError(null);
          const fd = new FormData(e.currentTarget);
          const password = String(fd.get("password"));
          const confirm = String(fd.get("confirm"));
          if (password !== confirm) {
            setError("Passwords do not match");
            return;
          }
          setLoading(true);
          try {
            await signUp(String(fd.get("email")), password, String(fd.get("full_name")));
            void navigate({ to: "/chat" });
          } catch (err) {
            setError(err instanceof Error ? err.message : "Sign up failed");
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
        <Field label="Full name">
          <input name="full_name" className="input" required defaultValue="Chafic" />
        </Field>
        <Field label="Email">
          <input name="email" type="email" className="input" required />
        </Field>
        <Field label="Password">
          <input name="password" type="password" className="input" required minLength={8} />
        </Field>
        <Field label="Confirm password">
          <input name="confirm" type="password" className="input" required minLength={8} />
        </Field>
        <button className="btn-primary w-full" disabled={loading}>
          {loading ? "Creating account…" : "Create account"}
        </button>
        <p className="text-center text-sm text-muted-foreground">
          Already have an account?{" "}
          <Link to="/login" className="text-primary hover:underline">
            Log in
          </Link>
        </p>
      </form>
    </AuthShell>
  );
}
