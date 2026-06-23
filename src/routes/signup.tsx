import { createFileRoute, Link } from "@tanstack/react-router";
import { AuthShell, Field } from "./login";

export const Route = createFileRoute("/signup")({
  head: () => ({ meta: [{ title: "Sign up — MultiAI" }] }),
  component: SignupPage,
});

function SignupPage() {
  return (
    <AuthShell title="Create your account" subtitle="Start comparing AI answers in under a minute.">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          window.location.href = "/onboarding";
        }}
        className="space-y-4"
      >
        <Field label="Full name">
          <input className="input" defaultValue="Sara Kassem" />
        </Field>
        <Field label="Email">
          <input type="email" className="input" defaultValue="sara@acme.co" />
        </Field>
        <Field label="Password">
          <input type="password" className="input" defaultValue="strongpass" />
        </Field>
        <Field label="Confirm password">
          <input type="password" className="input" defaultValue="strongpass" />
        </Field>
        <button className="btn-primary w-full">Create account</button>
        <div className="relative my-2 text-center text-xs text-muted-foreground">
          <span className="bg-card px-2 relative z-10">or</span>
          <div className="absolute inset-x-0 top-1/2 -z-0 h-px bg-border" />
        </div>
        <button type="button" className="btn-outline w-full">
          Continue with Google
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
