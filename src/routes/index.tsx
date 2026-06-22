import { createFileRoute, Link } from "@tanstack/react-router";
import {
  Sparkles,
  Layers,
  Gavel,
  FileSpreadsheet,
  Link2,
  Upload,
  Wand2,
  LayoutTemplate,
  ArrowRight,
  Check,
} from "lucide-react";
import { MODELS } from "@/lib/mock";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "MultiAI — Ask once, compare answers, get the best verdict" },
      { name: "description", content: "MultiAI runs your question through several models side-by-side, then a Verdict AI compares, reconciles or combines the best answers." },
      { property: "og:title", content: "MultiAI — Ask once, compare answers, get the best verdict" },
      { property: "og:description", content: "Compare AI answers side-by-side and get one trustworthy verdict." },
    ],
  }),
  component: Landing,
});

const FEATURES = [
  { icon: Layers, title: "Multiple AI answers", text: "Send one prompt to several models and read them side-by-side." },
  { icon: Gavel, title: "Verdict AI", text: "A judge model reconciles, ranks or combines the best response." },
  { icon: Sparkles, title: "Model Sets", text: "Save curated bundles for coding, business or research." },
  { icon: LayoutTemplate, title: "Templates", text: "Reusable instructions that shape every answer." },
  { icon: Upload, title: "File & image uploads", text: "Drop in PDFs, screenshots or sheets as context." },
  { icon: Link2, title: "Chat references", text: "Link previous chats so context flows forward." },
  { icon: FileSpreadsheet, title: "Excel preview", text: "Generate spreadsheets and download them in one click." },
  { icon: Wand2, title: "Prompt Builder", text: "Polish a rough idea into a structured, high-quality prompt." },
];

function Landing() {
  return (
    <div className="min-h-screen bg-background">
      <header className="sticky top-0 z-20 border-b border-border/60 bg-background/80 backdrop-blur">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-6">
          <Link to="/" className="flex items-center gap-2 font-display text-lg font-semibold">
            <span className="grid size-8 place-items-center rounded-xl bg-primary text-primary-foreground"><Sparkles className="size-4" /></span>
            MultiAI
          </Link>
          <nav className="hidden gap-7 text-sm text-muted-foreground md:flex">
            <a href="#features" className="hover:text-foreground">Features</a>
            <a href="#preview" className="hover:text-foreground">How it works</a>
            <Link to="/admin" className="hover:text-foreground">For teams</Link>
          </nav>
          <div className="flex items-center gap-2">
            <Link to="/login" className="rounded-lg px-3 py-2 text-sm text-muted-foreground hover:text-foreground">Log in</Link>
            <Link to="/signup" className="rounded-lg bg-primary px-3.5 py-2 text-sm font-medium text-primary-foreground hover:opacity-90">Get started</Link>
          </div>
        </div>
      </header>

      {/* Hero */}
      <section className="relative overflow-hidden">
        <div className="absolute inset-0 -z-10 bg-[radial-gradient(60%_60%_at_50%_0%,oklch(0.92_0.06_200)_0%,transparent_70%)]" />
        <div className="mx-auto max-w-5xl px-6 pt-20 pb-16 text-center">
          <div className="mx-auto inline-flex items-center gap-2 rounded-full border border-border bg-card px-3 py-1 text-xs text-muted-foreground shadow-sm">
            <span className="size-1.5 rounded-full bg-success" /> Now with Verdict AI v2
          </div>
          <h1 className="mx-auto mt-6 max-w-3xl text-balance text-5xl font-semibold leading-[1.05] md:text-6xl">
            Ask once. Compare every model. <span className="text-primary">Get one verdict.</span>
          </h1>
          <p className="mx-auto mt-5 max-w-2xl text-balance text-lg text-muted-foreground">
            MultiAI runs your question through GPT, Claude, Gemini and more — then a Verdict AI reconciles, ranks or combines the best answer for you.
          </p>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
            <Link to="/signup" className="inline-flex items-center gap-2 rounded-xl bg-primary px-5 py-3 text-sm font-medium text-primary-foreground shadow hover:opacity-90">
              Get started <ArrowRight className="size-4" />
            </Link>
            <Link to="/chat" className="rounded-xl border border-border bg-card px-5 py-3 text-sm font-medium hover:bg-accent">
              Try the demo
            </Link>
          </div>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-x-6 gap-y-2 text-xs text-muted-foreground">
            {MODELS.slice(0, 6).map((m) => (
              <span key={m.id} className="inline-flex items-center gap-1.5">
                <span className="size-2 rounded-full" style={{ background: m.color }} /> {m.name}
              </span>
            ))}
          </div>
        </div>

        {/* Preview mock */}
        <div id="preview" className="mx-auto max-w-5xl px-6 pb-20">
          <div className="overflow-hidden rounded-2xl border border-border bg-card shadow-xl">
            <div className="flex items-center gap-2 border-b border-border px-4 py-3">
              <span className="size-2.5 rounded-full bg-destructive/60" />
              <span className="size-2.5 rounded-full bg-warning" />
              <span className="size-2.5 rounded-full bg-success" />
              <span className="ml-3 text-xs text-muted-foreground">Balanced Set · Synthesize</span>
            </div>
            <div className="grid gap-4 p-5 md:grid-cols-3">
              {MODELS.slice(0, 3).map((m, i) => (
                <div key={m.id} className="rounded-xl border border-border bg-background p-4">
                  <div className="flex items-center gap-2">
                    <span className="size-2 rounded-full" style={{ background: m.color }} />
                    <span className="text-sm font-medium">{m.name}</span>
                    <span className="ml-auto text-xs text-muted-foreground">{90 - i * 4}%</span>
                  </div>
                  <div className="mt-3 space-y-1.5">
                    <div className="h-2 w-full rounded bg-muted" />
                    <div className="h-2 w-11/12 rounded bg-muted" />
                    <div className="h-2 w-9/12 rounded bg-muted" />
                  </div>
                </div>
              ))}
            </div>
            <div className="border-t border-border bg-accent/30 p-5">
              <div className="flex items-center gap-2 text-sm font-medium"><Gavel className="size-4 text-primary" /> Verdict — Synthesize</div>
              <div className="mt-2 space-y-1.5">
                <div className="h-2 w-full rounded bg-muted" />
                <div className="h-2 w-11/12 rounded bg-muted" />
                <div className="h-2 w-8/12 rounded bg-muted" />
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Features */}
      <section id="features" className="border-t border-border bg-sidebar/40 py-20">
        <div className="mx-auto max-w-6xl px-6">
          <div className="max-w-2xl">
            <h2 className="text-3xl font-semibold md:text-4xl">Everything you need to trust your AI answers</h2>
            <p className="mt-3 text-muted-foreground">Built for people who use AI for real work — not just for fun.</p>
          </div>
          <div className="mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {FEATURES.map((f) => (
              <div key={f.title} className="rounded-2xl border border-border bg-card p-5">
                <div className="grid size-9 place-items-center rounded-lg bg-accent text-accent-foreground">
                  <f.icon className="size-4" />
                </div>
                <div className="mt-4 font-medium">{f.title}</div>
                <div className="mt-1 text-sm text-muted-foreground">{f.text}</div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="py-20">
        <div className="mx-auto max-w-3xl rounded-3xl border border-border bg-card px-8 py-12 text-center shadow-sm">
          <h3 className="text-3xl font-semibold">Stop guessing which model is right.</h3>
          <p className="mx-auto mt-3 max-w-xl text-muted-foreground">Let MultiAI run them in parallel and hand you a single, well-reasoned answer.</p>
          <div className="mt-6 flex flex-wrap justify-center gap-3">
            <Link to="/signup" className="inline-flex items-center gap-2 rounded-xl bg-primary px-5 py-3 text-sm font-medium text-primary-foreground">Create free account</Link>
            <Link to="/chat" className="rounded-xl border border-border px-5 py-3 text-sm font-medium hover:bg-accent">Try the demo</Link>
          </div>
          <ul className="mx-auto mt-6 flex flex-wrap justify-center gap-x-6 gap-y-1 text-xs text-muted-foreground">
            {["No credit card", "Free during beta", "Cancel anytime"].map((x) => (
              <li key={x} className="inline-flex items-center gap-1.5"><Check className="size-3.5 text-success" /> {x}</li>
            ))}
          </ul>
        </div>
      </section>

      <footer className="border-t border-border py-8 text-center text-xs text-muted-foreground">
        © 2026 MultiAI — A prototype. Not affiliated with model providers.
      </footer>
    </div>
  );
}
