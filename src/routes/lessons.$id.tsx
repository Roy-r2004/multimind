import { createFileRoute, Link } from "@tanstack/react-router";
import {
  ArrowLeft,
  BookMarked,
  Brain,
  CheckCircle2,
  Gavel,
  Scale,
  User,
  XCircle,
} from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { GlassCard } from "@/components/cinematic/PageChrome";
import { MockBanner } from "@/components/cinematic/MockBanner";
import { SkeletonReveal } from "@/components/cinematic/SkeletonReveal";
import { getMockLesson } from "@/lib/mock-preview";

export const Route = createFileRoute("/lessons/$id")({
  head: () => ({ meta: [{ title: "Lesson — MultiAI" }] }),
  component: LessonDetailPage,
});

const MODEL_COLORS: Record<string, string> = {
  "gpt-4.1": "oklch(0.55 0.12 145)",
  claude: "oklch(0.55 0.14 35)",
  gemini: "oklch(0.55 0.14 250)",
};

function LessonDetailPage() {
  const { id } = Route.useParams();
  const lesson = getMockLesson(id);

  if (!lesson) {
    return (
      <AppShell>
        <div className="mx-auto max-w-3xl px-6 py-16 text-center">
          <p className="text-muted-foreground">Mock lesson not found.</p>
          <Link to="/lessons" className="mt-4 inline-block text-sm text-primary hover:underline">
            Back to lessons
          </Link>
        </div>
      </AppShell>
    );
  }

  const c = lesson.comparison;
  const modelColor = MODEL_COLORS[lesson.verdict_model_id] ?? "oklch(0.55 0.1 250)";
  const firstName = lesson.user_name.split(" ")[0] || lesson.user_name;

  return (
    <AppShell>
      <div className="relative overflow-hidden">
        <div className="pointer-events-none absolute inset-x-0 top-0 h-72 bg-[radial-gradient(ellipse_80%_60%_at_50%_-10%,oklch(0.58_0.14_240/0.14),transparent)]" />

        <div className="relative mx-auto max-w-6xl px-6 py-10">
          <Link
            to="/lessons"
            className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="size-4" /> All lessons
          </Link>

          <MockBanner className="mt-4">
            {" "}
            — detailed &quot;{firstName} vs {lesson.verdict_model_name}&quot; preview.
          </MockBanner>

          {/* Cinematic VS hero */}
          <div className="mt-8 animate-fade-up overflow-hidden rounded-3xl border border-border bg-card shadow-sm">
            <div className="grid md:grid-cols-[1fr_auto_1fr]">
              <div className="border-b border-border bg-gradient-to-br from-sky-50 to-white p-8 md:border-b-0 md:border-r">
                <div className="flex items-center gap-2 text-primary">
                  <User className="size-5" />
                  <span className="text-xs font-semibold uppercase tracking-widest">{firstName}</span>
                </div>
                <h1 className="mt-3 font-display text-2xl font-bold tracking-tight">Your position</h1>
                <p className="mt-2 text-sm leading-relaxed text-muted-foreground line-clamp-4">
                  {c.user_position_summary || lesson.user_position}
                </p>
              </div>

              <div className="relative flex items-center justify-center bg-muted/30 px-6 py-4 md:py-0">
                <div className="absolute inset-0 bg-[repeating-linear-gradient(-45deg,transparent,transparent_8px,oklch(0.58_0.14_240/0.03)_8px,oklch(0.58_0.14_240/0.03)_16px)]" />
                <span className="relative font-display text-3xl font-black tracking-tighter text-gradient">VS</span>
              </div>

              <div className="border-t border-border p-8 md:border-t-0 md:border-l">
                <div className="flex items-center gap-2 text-muted-foreground">
                  <Gavel className="size-5" style={{ color: modelColor }} />
                  <span className="text-xs font-semibold uppercase tracking-widest">{lesson.verdict_model_name}</span>
                </div>
                <h1 className="mt-3 font-display text-2xl font-bold tracking-tight">AI verdict</h1>
                <p className="mt-2 text-sm leading-relaxed text-muted-foreground line-clamp-4">
                  {c.model_position_summary || lesson.verdict_text}
                </p>
              </div>
            </div>
            <div className="border-t border-border px-6 py-3 text-center text-sm text-muted-foreground">
              {lesson.title} · {lesson.strategy}
            </div>
          </div>

          <SkeletonReveal delayMs={300} className="mt-6">
            <GlassCard className="p-5">
              <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Original question</p>
              <p className="mt-2 text-sm leading-relaxed">{lesson.user_message}</p>
            </GlassCard>
          </SkeletonReveal>

          {c.overview && (
            <SkeletonReveal delayMs={400} className="mt-4">
              <GlassCard className="p-5">
                <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Overview</p>
                <p className="mt-2 text-sm leading-relaxed">{c.overview}</p>
              </GlassCard>
            </SkeletonReveal>
          )}

          <SkeletonReveal delayMs={500} className="mt-8">
            <Section title="Why you disagreed">
              <p className="text-sm leading-relaxed text-muted-foreground">{lesson.disagreement_reason}</p>
            </Section>
          </SkeletonReveal>

          {c.agreements.length > 0 && (
            <Section
              title="Points of agreement"
              icon={<CheckCircle2 className="size-4 text-emerald-600" />}
              className="mt-8"
            >
              <div className="space-y-3">
                {c.agreements.map((item, i) => (
                  <div key={i} className="rounded-xl border border-emerald-200 bg-emerald-50/60 p-4">
                    <div className="font-medium text-emerald-900">{item.topic}</div>
                    <p className="mt-1 text-sm text-emerald-800/90">{item.detail}</p>
                  </div>
                ))}
              </div>
            </Section>
          )}

          {c.disagreements.length > 0 && (
            <Section title="Points of disagreement" icon={<Scale className="size-4 text-primary" />} className="mt-8">
              <div className="space-y-4">
                {c.disagreements.map((item, i) => (
                  <div key={i} className="overflow-hidden rounded-xl border border-border">
                    <div className="border-b border-border bg-muted/50 px-4 py-2.5 text-sm font-medium">
                      {item.topic}
                    </div>
                    <div className="grid md:grid-cols-2">
                      <div className="border-b border-border p-4 md:border-b-0 md:border-r">
                        <div className="text-[11px] font-semibold uppercase tracking-wide text-primary">
                          {firstName}
                        </div>
                        <p className="mt-2 text-sm leading-relaxed">{item.user_view}</p>
                      </div>
                      <div className="p-4">
                        <div className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                          {lesson.verdict_model_name}
                        </div>
                        <p className="mt-2 text-sm leading-relaxed">{item.model_view}</p>
                      </div>
                    </div>
                    <div className="border-t border-border bg-sky-50/50 px-4 py-3 text-sm">
                      <span className="font-medium">Analysis: </span>
                      {item.analysis}
                    </div>
                  </div>
                ))}
              </div>
            </Section>
          )}

          {c.lesson.headline && (
            <GlassCard glow className="mt-8 p-6">
              <div className="flex items-center gap-2 text-primary">
                <BookMarked className="size-5" />
                <span className="text-sm font-semibold uppercase tracking-wide">Lesson absorbed into brain</span>
              </div>
              <h3 className="mt-3 text-xl font-semibold">{c.lesson.headline}</h3>
              {c.lesson.key_insight && (
                <p className="mt-3 text-sm leading-relaxed text-muted-foreground">{c.lesson.key_insight}</p>
              )}
              {c.lesson.what_to_remember.length > 0 && (
                <ul className="mt-4 space-y-2">
                  {c.lesson.what_to_remember.map((item, i) => (
                    <li key={i} className="flex gap-2 text-sm">
                      <Brain className="mt-0.5 size-4 shrink-0 text-primary" />
                      <span>{item}</span>
                    </li>
                  ))}
                </ul>
              )}
              {c.lesson.recommended_next_step && (
                <div className="mt-4 rounded-xl border border-primary/20 bg-primary/5 p-4 text-sm">
                  <span className="font-medium text-primary">Next step: </span>
                  {c.lesson.recommended_next_step}
                </div>
              )}
            </GlassCard>
          )}

          <div className="mt-8 flex flex-wrap gap-3">
            <Link to="/brain" className="rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground">
              View {firstName}&apos;s brain
            </Link>
            <Link to="/lessons" className="rounded-xl border border-border px-4 py-2 text-sm hover:bg-accent">
              All lessons
            </Link>
          </div>
        </div>
      </div>
    </AppShell>
  );
}

function Section({
  title,
  icon,
  children,
  className,
}: {
  title: string;
  icon?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={className}>
      <div className="mb-3 flex items-center gap-2">
        {icon}
        <h2 className="text-lg font-semibold">{title}</h2>
      </div>
      <GlassCard className="p-5">{children}</GlassCard>
    </section>
  );
}
