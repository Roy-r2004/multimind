import { createFileRoute, Link } from "@tanstack/react-router";
import { Gavel, Scale, User } from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { GlassCard, PageHeader } from "@/components/cinematic/PageChrome";
import { MockBanner } from "@/components/cinematic/MockBanner";
import { SkeletonReveal } from "@/components/cinematic/SkeletonReveal";
import { MOCK_LESSONS } from "@/lib/mock-preview";

export const Route = createFileRoute("/lessons")({
  head: () => ({ meta: [{ title: "Lessons — MultiAI" }] }),
  component: LessonsPage,
});

function LessonsPage() {
  return (
    <AppShell>
      <div className="relative mx-auto max-w-5xl px-6 py-10">
        <div className="pointer-events-none absolute inset-x-0 top-0 h-64 bg-[radial-gradient(ellipse_60%_80%_at_50%_-20%,oklch(0.58_0.14_240/0.1),transparent)]" />

        <MockBanner>
          {" "}
          — sample &quot;Chafic vs Model&quot; lessons. Not connected to live data yet.
        </MockBanner>

        <PageHeader
          className="relative mt-6 animate-fade-up"
          eyebrow="Learning"
          title="Verdict lessons"
          description="Every disagreement becomes a detailed comparison — you vs the model — and feeds your brain."
        />

        <div className="relative mt-8 space-y-4">
          {MOCK_LESSONS.map((lesson, i) => (
            <SkeletonReveal key={lesson.id} delayMs={200 + i * 150}>
              <Link to="/lessons/$id" params={{ id: lesson.id }}>
                <GlassCard className="group overflow-hidden p-0 transition hover:border-primary/40 hover:shadow-md">
                  <div className="grid md:grid-cols-[1fr_auto]">
                    <div className="p-5">
                      <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.2em] text-primary">
                        <Scale className="size-3.5" />
                        Disagreement lesson
                      </div>
                      <h3 className="mt-2 text-lg font-semibold group-hover:text-primary">{lesson.title}</h3>
                      <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">{lesson.summary}</p>
                      <p className="mt-3 text-xs text-muted-foreground">
                        {new Date(lesson.created_at).toLocaleDateString()}
                      </p>
                    </div>

                    <div className="flex min-w-[200px] flex-col justify-center border-t border-border bg-gradient-to-br from-sky-50/80 to-white p-5 md:border-t-0 md:border-l">
                      <VsBadge
                        user={lesson.user_name.split(" ")[0]}
                        model={lesson.verdict_model_name}
                      />
                    </div>
                  </div>
                </GlassCard>
              </Link>
            </SkeletonReveal>
          ))}
        </div>

        <Link to="/brain" className="relative mt-8 inline-block text-sm text-primary hover:underline">
          See how lessons feed Chafic&apos;s brain →
        </Link>
      </div>
    </AppShell>
  );
}

function VsBadge({ user, model }: { user: string; model: string }) {
  return (
    <div className="flex items-center justify-center gap-2 text-sm">
      <span className="flex items-center gap-1.5 rounded-lg border border-primary/20 bg-primary/5 px-2 py-1 font-medium text-primary">
        <User className="size-3.5" />
        {user}
      </span>
      <span className="text-xs font-bold uppercase tracking-widest text-muted-foreground">vs</span>
      <span className="flex items-center gap-1.5 rounded-lg border border-border bg-card px-2 py-1 font-medium">
        <Gavel className="size-3.5 text-muted-foreground" />
        <span className="max-w-[5rem] truncate text-xs">{model}</span>
      </span>
    </div>
  );
}
