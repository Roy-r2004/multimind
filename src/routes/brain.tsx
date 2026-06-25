import { createFileRoute, Link } from "@tanstack/react-router";
import { Heart, ThumbsDown, Zap, Activity, Sparkles } from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { BrainVisualization } from "@/components/cinematic/BrainVisualization";
import { GlassCard } from "@/components/cinematic/PageChrome";
import { MockBanner } from "@/components/cinematic/MockBanner";
import { SkeletonReveal } from "@/components/cinematic/SkeletonReveal";
import { MOCK_BRAIN } from "@/lib/mock-preview";

export const Route = createFileRoute("/brain")({
  head: () => ({ meta: [{ title: "Brain — MultiAI" }] }),
  component: BrainPage,
});

function BrainPage() {
  const brain = MOCK_BRAIN;
  const firstName = brain.user_name.split(" ")[0];

  return (
    <AppShell>
      <div className="relative overflow-hidden">
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_70%_50%_at_50%_-10%,oklch(0.58_0.14_240/0.12),transparent)]" />

        <div className="relative mx-auto max-w-6xl px-6 py-10">
          <MockBanner>
            {" "}
            — cinematic preview of Chafic&apos;s neural memory. Not wired to the API yet.
          </MockBanner>

          <div className="mt-8 grid items-center gap-10 lg:grid-cols-[1fr_1.1fr]">
            <div className="animate-fade-up order-2 lg:order-1">
              <p className="text-[11px] font-semibold uppercase tracking-[0.32em] text-primary">Third brain</p>
              <h1 className="mt-2 font-display text-4xl font-bold tracking-tight md:text-5xl">
                {firstName}&apos;s
                <br />
                <span className="text-gradient">living memory</span>
              </h1>
              <p className="mt-4 max-w-lg text-sm leading-relaxed text-muted-foreground">
                A skeleton of how you think — wired from every verdict you reject. Fed back into the council so
                models learn your taste, not generic best practices.
              </p>

              <div className="mt-6 grid grid-cols-3 gap-3">
                <StatPill icon={<Activity className="size-3.5" />} label="Memories" value={String(brain.lesson_count)} />
                <StatPill icon={<Heart className="size-3.5" />} label="Prefers" value={String(brain.likes.length)} />
                <StatPill
                  icon={<ThumbsDown className="size-3.5" />}
                  label="Rejects"
                  value={String(brain.dislikes.length)}
                />
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

          <div className="mt-14 space-y-8">
            <SkeletonReveal delayMs={400}>
              <GlassCard glow className="p-6">
                <p className="text-xs font-semibold uppercase tracking-wide text-primary">Cognitive profile</p>
                <p className="mt-3 text-sm leading-relaxed">{brain.summary}</p>
                <p className="mt-4 rounded-xl border border-sky-200/80 bg-sky-50/60 p-4 text-sm leading-relaxed">
                  <span className="font-medium text-foreground">Reasoning style · </span>
                  {brain.thinking_style}
                </p>
              </GlassCard>
            </SkeletonReveal>

            <div className="grid gap-4 md:grid-cols-2">
              <SkeletonReveal delayMs={550}>
                <PreferencePanel
                  title="Neural prefers"
                  icon={<Heart className="size-4 text-emerald-600" />}
                  items={brain.likes}
                  tone="like"
                />
              </SkeletonReveal>
              <SkeletonReveal delayMs={700}>
                <PreferencePanel
                  title="Neural rejects"
                  icon={<ThumbsDown className="size-4 text-rose-600" />}
                  items={brain.dislikes}
                  tone="dislike"
                />
              </SkeletonReveal>
            </div>

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
                        <div className="mt-3 flex flex-wrap gap-1.5">
                          {m.likes.map((t) => (
                            <span
                              key={t}
                              className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[11px] text-emerald-800"
                            >
                              + {t}
                            </span>
                          ))}
                          {m.dislikes.map((t) => (
                            <span
                              key={t}
                              className="rounded-full border border-rose-200 bg-rose-50 px-2 py-0.5 text-[11px] text-rose-800"
                            >
                              − {t}
                            </span>
                          ))}
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
          </div>
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

function PreferencePanel({
  title,
  icon,
  items,
  tone,
}: {
  title: string;
  icon: React.ReactNode;
  items: string[];
  tone: "like" | "dislike";
}) {
  return (
    <GlassCard className="p-5">
      <div className="flex items-center gap-2 text-sm font-medium">
        {icon}
        {title}
      </div>
      <ul className="mt-4 space-y-2">
        {items.map((item, i) => (
          <li
            key={item}
            className="flex items-start gap-2 text-sm animate-fade-up"
            style={{ animationDelay: `${i * 80}ms` }}
          >
            <span
              className={
                tone === "like"
                  ? "mt-1.5 size-1.5 shrink-0 rounded-full bg-emerald-500"
                  : "mt-1.5 size-1.5 shrink-0 rounded-full bg-rose-500"
              }
            />
            <span className="text-muted-foreground">{item}</span>
          </li>
        ))}
      </ul>
    </GlassCard>
  );
}
