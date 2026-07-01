import { createFileRoute, Link } from "@tanstack/react-router";
import { useCallback, useEffect, useState } from "react";
import { Gavel, Loader2, Scale, Trash2, User } from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { Modal } from "@/components/Modal";
import { GlassCard, PageHeader } from "@/components/cinematic/PageChrome";
import { SkeletonReveal } from "@/components/cinematic/SkeletonReveal";
import { api } from "@/lib/api";
import type { ApiLessonListItem } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";

export const Route = createFileRoute("/lessons")({
  head: () => ({ meta: [{ title: "Lessons — MultiAI" }] }),
  component: LessonsPage,
});

function LessonsPage() {
  const { authHeaders, isLoading: authLoading } = useAuth();
  const [lessons, setLessons] = useState<ApiLessonListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [removeTarget, setRemoveTarget] = useState<ApiLessonListItem | null>(null);
  const [removing, setRemoving] = useState(false);

  const load = useCallback(async () => {
    if (authLoading) return;
    const auth = authHeaders();
    if (!auth) {
      setLessons([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      setLessons(await api.lessons.list(auth));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load lessons");
    } finally {
      setLoading(false);
    }
  }, [authHeaders, authLoading]);

  useEffect(() => {
    void load();
  }, [load]);

  async function removeLesson() {
    const auth = authHeaders();
    if (!auth || !removeTarget) return;
    setRemoving(true);
    try {
      await api.lessons.delete(auth, removeTarget.id);
      setRemoveTarget(null);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to delete lesson");
    } finally {
      setRemoving(false);
    }
  }

  return (
    <AppShell>
      <div className="relative mx-auto max-w-5xl px-6 py-10">
        <div className="pointer-events-none absolute inset-x-0 top-0 h-64 bg-[radial-gradient(ellipse_60%_80%_at_50%_-20%,oklch(0.58_0.14_240/0.1),transparent)]" />

        <PageHeader
          className="relative animate-fade-up"
          eyebrow="Learning"
          title="Verdict lessons"
          description="Every disagreement becomes a detailed comparison — you vs the model — and feeds your brain."
        />

        {loading ? (
          <div className="flex justify-center py-20">
            <Loader2 className="size-6 animate-spin text-muted-foreground" />
          </div>
        ) : error ? (
          <GlassCard className="mt-8 p-8 text-center text-sm text-destructive">{error}</GlassCard>
        ) : lessons.length === 0 ? (
          <GlassCard className="mt-8 p-10 text-center">
            <p className="text-sm text-muted-foreground">No lessons yet.</p>
            <p className="mt-2 text-sm text-muted-foreground">
              Disagree with a verdict in chat to create your first lesson.
            </p>
            <Link to="/chat" className="mt-4 inline-block text-sm font-medium text-primary hover:underline">
              Go to chat →
            </Link>
          </GlassCard>
        ) : (
          <div className="relative mt-8 space-y-4">
            {lessons.map((lesson, i) => (
              <SkeletonReveal key={lesson.id} delayMs={200 + i * 150}>
                <GlassCard className="group overflow-hidden p-0 transition hover:border-primary/40 hover:shadow-md">
                  <div className="grid md:grid-cols-[1fr_auto]">
                    <Link to="/lessons/$id" params={{ id: lesson.id }} className="block p-5">
                      <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.2em] text-primary">
                        <Scale className="size-3.5" />
                        Disagreement lesson
                        {lesson.status !== "completed" && (
                          <span className="rounded-full bg-amber-100 px-2 py-0.5 text-amber-800 capitalize">
                            {lesson.status}
                          </span>
                        )}
                      </div>
                      <h3 className="mt-2 text-lg font-semibold group-hover:text-primary">{lesson.title}</h3>
                      <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">{lesson.summary}</p>
                      <p className="mt-3 text-xs text-muted-foreground">
                        {new Date(lesson.created_at).toLocaleDateString()}
                      </p>
                    </Link>

                    <div className="flex min-w-[200px] flex-col items-center justify-center gap-3 border-t border-border bg-gradient-to-br from-sky-50/80 to-white p-5 md:border-t-0 md:border-l">
                      <VsBadge
                        user={lesson.user_name.split(" ")[0]}
                        model={lesson.verdict_model_name}
                      />
                      <button
                        type="button"
                        onClick={() => setRemoveTarget(lesson)}
                        className="inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-xs text-destructive hover:bg-destructive/10"
                      >
                        <Trash2 className="size-3.5" /> Delete
                      </button>
                    </div>
                  </div>
                </GlassCard>
              </SkeletonReveal>
            ))}
          </div>
        )}

        <Link to="/brain" className="relative mt-8 inline-block text-sm text-primary hover:underline">
          See how lessons feed your brain →
        </Link>
      </div>

      <Modal open={!!removeTarget} onClose={() => setRemoveTarget(null)} title="Delete lesson?" size="sm">
        <p className="text-sm text-muted-foreground">
          {removeTarget
            ? `"${removeTarget.title}" will be permanently removed.`
            : "This lesson will be permanently removed."}
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={() => setRemoveTarget(null)}
            className="rounded-lg border border-border px-4 py-2 text-sm hover:bg-accent"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={removing}
            onClick={() => void removeLesson()}
            className="rounded-lg bg-destructive px-4 py-2 text-sm font-medium text-destructive-foreground disabled:opacity-50"
          >
            {removing ? "Deleting…" : "Delete"}
          </button>
        </div>
      </Modal>
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
