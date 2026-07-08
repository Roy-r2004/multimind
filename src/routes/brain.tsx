import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { Loader2, Zap, Activity, Sparkles } from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { BrainVisualization } from "@/components/cinematic/BrainVisualization";
import { GlassCard } from "@/components/cinematic/PageChrome";
import { SkeletonReveal } from "@/components/cinematic/SkeletonReveal";
import { api } from "@/lib/api";
import type { ApiBrain } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";

export const Route = createFileRoute("/brain")({
  head: () => ({ meta: [{ title: "Brain — MultiAI" }] }),
  component: BrainPage,
});

function BrainPage() {
  const { authHeaders } = useAuth();
  const [brain, setBrain] = useState<ApiBrain | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const auth = authHeaders();
    if (!auth) {
      setLoading(false);
      return;
    }
    setLoading(true);
    void api.brain
      .get(auth)
      .then(setBrain)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load brain"))
      .finally(() => setLoading(false));
  }, [authHeaders]);

  if (loading) {
    return (
      <AppShell>
        <div className="flex justify-center py-24">
          <Loader2 className="size-6 animate-spin text-muted-foreground" />
        </div>
      </AppShell>
    );
  }

  if (error || !brain) {
    return (
      <AppShell>
        <div className="mx-auto max-w-lg px-6 py-20 text-center text-sm text-destructive">
          {error ?? "Could not load brain profile"}
        </div>
      </AppShell>
    );
  }

  const firstName = brain.user_name.split(" ")[0];
  const isEmpty =
    !brain.summary && !brain.thinking_style && brain.memories.length === 0;

  return (
    <AppShell>
      <div className="relative overflow-hidden">
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_70%_50%_at_50%_-10%,oklch(0.58_0.14_240/0.12),transparent)]" />

        <div className="relative mx-auto max-w-6xl px-6 py-10">
          <div className="mt-2 grid items-center gap-10 lg:grid-cols-[1fr_1.1fr]">
            <div className="animate-fade-up order-2 lg:order-1">
              <p className="text-[11px] font-semibold uppercase tracking-[0.32em] text-primary">Third brain</p>
              <h1 className="mt-2 font-display text-4xl font-bold tracking-tight md:text-5xl">
                {firstName}&apos;s
                <br />
                <span className="text-gradient">living memory</span>
              </h1>
              <p className="mt-4 max-w-lg text-sm leading-relaxed text-muted-foreground">
                Learned from every verdict you reject — fed back into the council so models match your taste.
              </p>

              <div className="mt-6">
                <StatPill icon={<Activity className="size-3.5" />} label="Lessons" value={String(brain.lesson_count)} />
              </div>

              <Link
                to="/lessons"
                className="mt-6 inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground shadow-sm transition hover:bg-primary/90"
              >
                <Sparkles className="size-4" /> View disagreement lessons
              </Link>
            </div>

            <div className="animate-fade-up-delay order-1 lg:order-2">
              <BrainVisualization name={brain.user_name} lessonCount={brain.lesson_count} />
            </div>
          </div>

          {isEmpty ? (
            <GlassCard className="mt-14 p-8 text-center">
              <p className="text-sm text-muted-foreground">
                No brain profile yet. Disagree with a verdict in chat to start building your memory.
              </p>
              <Link to="/chat" className="mt-4 inline-block text-sm font-medium text-primary hover:underline">
                Go to chat →
              </Link>
            </GlassCard>
          ) : (
            <div className="mt-14 space-y-8">
              <SkeletonReveal delayMs={400}>
                <GlassCard glow className="p-6">
                  <p className="text-xs font-semibold uppercase tracking-wide text-primary">Cognitive profile</p>
                  <p className="mt-3 text-sm leading-relaxed">{brain.summary || "—"}</p>
                  {brain.thinking_style && (
                    <p className="mt-4 rounded-xl border border-sky-200/80 bg-sky-50/60 p-4 text-sm leading-relaxed">
                      <span className="font-medium text-foreground">Reasoning style · </span>
                      {brain.thinking_style}
                    </p>
                  )}
                </GlassCard>
              </SkeletonReveal>

              {brain.memories.length > 0 && (
                <section>
                  <div className="mb-4 flex items-center gap-2">
                    <Zap className="size-4 text-primary" />
                    <h2 className="text-lg font-semibold">Synaptic log</h2>
                  </div>
                  <div className="relative space-y-3 pl-6 before:absolute before:left-[7px] before:top-2 before:h-[calc(100%-1rem)] before:w-px before:bg-gradient-to-b before:from-primary/40 before:via-primary/20 before:to-transparent">
                    {[...brain.memories].reverse().map((m, i) => (
                      <SkeletonReveal key={m.id} delayMs={800 + i * 120}>
                        <div className="relative">
                          <span className="absolute -left-6 top-4 size-3.5 rounded-full border-2 border-primary bg-background shadow-[0_0_8px_oklch(0.58_0.14_240/0.5)]" />
                          <GlassCard className="p-4 transition hover:border-primary/30">
                            <div className="flex flex-wrap items-start justify-between gap-2">
                              <div>
                                <div className="font-medium">{m.title}</div>
                                <p className="mt-1 text-sm text-muted-foreground">{m.insight}</p>
                              </div>
                              {m.created_at && (
                                <time className="text-xs text-muted-foreground">
                                  {new Date(m.created_at).toLocaleDateString()}
                                </time>
                              )}
                            </div>
                            {m.source_id && (
                              <Link
                                to="/lessons/$id"
                                params={{ id: m.source_id }}
                                className="mt-3 inline-block text-xs font-medium text-primary hover:underline"
                              >
                                Open lesson →
                              </Link>
                            )}
                          </GlassCard>
                        </div>
                      </SkeletonReveal>
                    ))}
                  </div>
                </section>
              )}
            </div>
          )}
        </div>
      </div>
    </AppShell>
  );
}

function StatPill({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-xl border border-border bg-card/80 p-3 text-center shadow-sm backdrop-blur-sm">
      <div className="mx-auto flex size-7 items-center justify-center rounded-lg bg-primary/10 text-primary">{icon}</div>
      <div className="mt-2 font-display text-xl font-bold">{value}</div>
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</div>
    </div>
  );
}
