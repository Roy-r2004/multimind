import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import {
  Loader2,
  Heart,
  ThumbsDown,
  Zap,
  Sparkles,
  BookOpen,
  Brain,
} from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { BrainVisualization } from "@/components/cinematic/BrainVisualization";
import { GlassCard } from "@/components/cinematic/PageChrome";
import { SkeletonReveal } from "@/components/cinematic/SkeletonReveal";
import { api } from "@/lib/api";
import type { ApiBrain } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/brain")({
  head: () => ({ meta: [{ title: "Brain — MultiAI" }] }),
  component: BrainPage,
});

function BrainPage() {
  const { authHeaders, session } = useAuth();
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
          <Loader2 className="size-6 animate-spin text-sky-300" />
        </div>
      </AppShell>
    );
  }

  if (error || !brain) {
    return (
      <AppShell>
        <div className="mx-auto max-w-lg px-6 py-20 text-center text-sm text-rose-300">
          {error ?? "Could not load brain profile"}
        </div>
      </AppShell>
    );
  }

  const knowledgeItems = brain.knowledge_items ?? [];
  const knowledgeCount = brain.knowledge_count ?? knowledgeItems.length;
  const memoriesIndexed = brain.memories.length;
  const prefs = brain.likes.length;
  const rejects = brain.dislikes.length;
  const styleTags = parseStyleTags(brain.thinking_style);
  const quote =
    brain.summary?.trim() ||
    "I don't collect information. I refine what's useful.";
  const bio =
    session?.user.full_name
      ? "Systems thinker. Builder. Clarity over noise."
      : "Personal memory. Structured intelligence.";

  return (
    <AppShell>
      <div className="relative mx-auto max-w-7xl px-4 py-8 md:px-6">
        <div className="mb-6">
          <h1 className="font-display text-3xl tracking-tight text-white md:text-4xl">
            Third Brain
          </h1>
          <p className="mt-1 text-sm text-slate-400">Personal memory. Structured intelligence.</p>
        </div>

        <div className="grid gap-4 lg:grid-cols-[240px_minmax(0,1fr)_300px]">
          {/* Left stats */}
          <div className="space-y-3">
            <StatCard
              icon={<Brain className="size-4" />}
              tone="sky"
              label="Memories indexed"
              value={String(memoriesIndexed)}
              hint="Lesson-linked memories"
            />
            <StatCard
              icon={<Sparkles className="size-4" />}
              tone="violet"
              label="Knowledge"
              value={String(knowledgeCount)}
              hint="Pinned sources & docs"
            />
            <StatCard
              icon={<Heart className="size-4" />}
              tone="emerald"
              label="Preferences"
              value={String(prefs)}
              hint="What you tend to keep"
            />
            <StatCard
              icon={<BookOpen className="size-4" />}
              tone="amber"
              label="Lessons"
              value={String(brain.lesson_count)}
              hint="Challenge outcomes"
            />
            <StatCard
              icon={<ThumbsDown className="size-4" />}
              tone="rose"
              label="Rejections"
              value={String(rejects)}
              hint="Patterns you avoid"
            />
          </div>

          {/* Center map */}
          <GlassCard variant="council" className="flex min-h-[420px] flex-col items-center justify-center p-4">
            <p className="mb-2 text-[10px] font-semibold uppercase tracking-[0.35em] text-sky-300/80">
              Intelligence map
            </p>
            <BrainVisualization
              name={brain.user_name}
              lessonCount={memoriesIndexed}
              className="max-w-lg"
            />
          </GlassCard>

          {/* Right profile */}
          <div className="space-y-4">
            <GlassCard variant="council" className="p-5">
              <div className="flex items-center gap-3">
                <span className="grid size-14 place-items-center rounded-full bg-gradient-to-br from-sky-400 to-violet-500 text-lg font-semibold text-white">
                  {brain.user_name.slice(0, 1).toUpperCase()}
                </span>
                <div>
                  <div className="font-medium text-white">{brain.user_name}</div>
                  <p className="text-xs text-slate-400">{bio}</p>
                </div>
              </div>

              <p className="mt-5 text-[11px] font-semibold uppercase tracking-[0.22em] text-sky-300/80">
                Cognitive profile
              </p>
              <div className="mt-3 space-y-3">
                {COGNITIVE_BARS.map((bar) => (
                  <ProgressRow key={bar.label} label={bar.label} value={bar.value} />
                ))}
              </div>

              {styleTags.length > 0 && (
                <>
                  <p className="mt-5 text-[11px] font-semibold uppercase tracking-[0.22em] text-sky-300/80">
                    Thinking style
                  </p>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {styleTags.map((tag) => (
                      <span
                        key={tag}
                        className="rounded-full border border-white/15 bg-white/5 px-2.5 py-1 text-[11px] text-slate-200"
                      >
                        {tag}
                      </span>
                    ))}
                  </div>
                </>
              )}

              <blockquote className="mt-5 rounded-xl border border-white/10 bg-white/5 px-3 py-3 text-sm italic text-slate-300">
                “{quote.length > 160 ? `${quote.slice(0, 160)}…` : quote}”
              </blockquote>
            </GlassCard>
          </div>
        </div>

        {/* Recent lessons / memories */}
        <div className="mt-8">
          <div className="mb-4 flex items-end justify-between gap-3">
            <div>
              <h2 className="font-display text-2xl text-white">Recent Lessons</h2>
              <p className="text-sm text-slate-400">Insights distilled from your challenges.</p>
            </div>
            <Link to="/lessons" className="text-sm font-medium text-sky-300 hover:text-sky-200">
              View all lessons
            </Link>
          </div>

          {brain.memories.length === 0 ? (
            <GlassCard variant="council" className="p-8 text-center">
              <p className="text-sm text-slate-400">
                No lessons yet. Challenge a verdict in chat to start building memory.
              </p>
              <Link
                to="/chat"
                className="council-glass-cta mt-4 inline-flex rounded-xl px-4 py-2 text-sm font-medium"
              >
                Go to chat
              </Link>
            </GlassCard>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {[...brain.memories]
                .reverse()
                .slice(0, 4)
                .map((m, i) => (
                  <SkeletonReveal key={m.id} delayMs={200 + i * 80}>
                    <GlassCard variant="council" className="flex h-full flex-col p-4">
                      <time className="text-[11px] text-slate-500">
                        {m.created_at
                          ? new Date(m.created_at).toLocaleDateString(undefined, {
                              month: "short",
                              day: "numeric",
                              year: "numeric",
                            })
                          : "—"}
                      </time>
                      <div className="mt-2 font-medium text-white">{m.title}</div>
                      <p className="mt-2 line-clamp-3 flex-1 text-sm text-slate-400">{m.insight}</p>
                      <span className="mt-3 inline-flex w-fit rounded-full border border-white/15 bg-white/5 px-2 py-0.5 text-[10px] uppercase tracking-wide text-slate-300">
                        {m.source === "lesson" ? "Insight" : m.source || "Memory"}
                      </span>
                      {m.source === "lesson" && m.source_id && (
                        <Link
                          to="/lessons/$id"
                          params={{ id: m.source_id }}
                          className="mt-2 text-xs font-medium text-sky-300 hover:underline"
                        >
                          Open lesson →
                        </Link>
                      )}
                    </GlassCard>
                  </SkeletonReveal>
                ))}
            </div>
          )}
        </div>

        <div className="mt-8 grid gap-4 md:grid-cols-2">
          <PreferencePanel
            title="Neural prefers"
            icon={<Heart className="size-4 text-emerald-300" />}
            items={brain.likes}
          />
          <PreferencePanel
            title="Neural rejects"
            icon={<ThumbsDown className="size-4 text-rose-300" />}
            items={brain.dislikes}
          />
        </div>

        {knowledgeItems.length > 0 && (
          <section className="mt-8">
            <div className="mb-4 flex items-center gap-2">
              <Zap className="size-4 text-sky-300" />
              <h2 className="text-lg font-semibold text-white">Knowledge sources</h2>
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              {knowledgeItems.slice(0, 6).map((item) => (
                <GlassCard key={item.id} variant="council" className="p-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="rounded-full bg-white/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-slate-300">
                      {item.source_type.replaceAll("_", " ")}
                    </span>
                    <div className="font-medium text-white">{item.title || item.source_id}</div>
                  </div>
                  <p className="mt-2 line-clamp-3 text-sm text-slate-400">{item.content}</p>
                </GlassCard>
              ))}
            </div>
          </section>
        )}
      </div>
    </AppShell>
  );
}

const COGNITIVE_BARS = [
  { label: "Reasoning Depth", value: 96 },
  { label: "Pattern Recognition", value: 92 },
  { label: "Strategic Foresight", value: 94 },
  { label: "Structured Thinking", value: 91 },
  { label: "Adaptability", value: 88 },
];

function parseStyleTags(style: string): string[] {
  if (!style?.trim()) {
    return ["First Principles", "Long-term", "Framework Driven", "Evidence-seeking", "High Standards"];
  }
  const parts = style
    .split(/[,;·|/]/)
    .map((s) => s.trim())
    .filter((s) => s.length > 2 && s.length < 40);
  if (parts.length >= 2) return parts.slice(0, 6);
  return ["First Principles", "Long-term", "Framework Driven", "Evidence-seeking"];
}

function ProgressRow({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <div className="mb-1 flex items-center justify-between text-xs">
        <span className="text-slate-300">{label}</span>
        <span className="tabular-nums text-slate-400">{value}%</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-white/10">
        <div
          className="h-full rounded-full bg-gradient-to-r from-sky-400 to-violet-500"
          style={{ width: `${value}%` }}
        />
      </div>
    </div>
  );
}

function StatCard({
  icon,
  label,
  value,
  hint,
  tone,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  hint: string;
  tone: "sky" | "violet" | "emerald" | "amber" | "rose";
}) {
  const tones = {
    sky: "bg-sky-400/15 text-sky-200",
    violet: "bg-violet-400/15 text-violet-200",
    emerald: "bg-emerald-400/15 text-emerald-200",
    amber: "bg-amber-400/15 text-amber-200",
    rose: "bg-rose-400/15 text-rose-200",
  };
  return (
    <GlassCard variant="council" className="p-4">
      <div className="flex items-start gap-3">
        <span className={cn("grid size-9 place-items-center rounded-full", tones[tone])}>
          {icon}
        </span>
        <div className="min-w-0">
          <div className="text-xs text-slate-400">{label}</div>
          <div className="font-display text-2xl text-white">{value}</div>
          <div className="text-[11px] text-slate-500">{hint}</div>
        </div>
      </div>
    </GlassCard>
  );
}

function PreferencePanel({
  title,
  icon,
  items,
}: {
  title: string;
  icon: React.ReactNode;
  items: string[];
}) {
  return (
    <GlassCard variant="council" className="p-5">
      <div className="flex items-center gap-2 text-sm font-medium text-white">
        {icon}
        {title}
      </div>
      {items.length === 0 ? (
        <p className="mt-4 text-sm text-slate-500">None yet.</p>
      ) : (
        <ul className="mt-4 space-y-2 text-sm text-slate-300">
          {items.map((item) => (
            <li key={item} className="rounded-lg border border-white/10 bg-white/5 px-3 py-2">
              {item}
            </li>
          ))}
        </ul>
      )}
    </GlassCard>
  );
}
