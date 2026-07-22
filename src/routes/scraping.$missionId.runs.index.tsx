import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useCallback, useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { GlassCard, PageHeader } from "@/components/cinematic/PageChrome";
import { Modal } from "@/components/Modal";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";
import { deleteScrapingRun, listScrapingRuns } from "@/lib/scraping/api";
import type {
  DeletableScrapingRunStatus,
  ScrapingRunStatus,
  ScrapingRunSummary,
} from "@/lib/scraping/types";

const DELETABLE_STATUSES = new Set<ScrapingRunStatus>([
  "planned",
  "completed",
  "failed",
  "cancelled",
]);

export const Route = createFileRoute("/scraping/$missionId/runs/")({
  head: () => ({ meta: [{ title: "Scraping Runs - MultiAI" }] }),
  component: ScrapingRunsPage,
});

function ScrapingRunsPage() {
  const { missionId } = Route.useParams();
  const { authHeaders } = useAuth();
  const navigate = useNavigate();
  const [runs, setRuns] = useState<ScrapingRunSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ScrapingRunSummary | null>(null);
  const [deleting, setDeleting] = useState(false);

  const loadRuns = useCallback(() => {
    const auth = authHeaders();
    if (!auth) {
      void navigate({ to: "/login" });
      return;
    }
    setLoading(true);
    setError(null);
    setDeleteError(null);
    void listScrapingRuns(auth, missionId)
      .then(setRuns)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load runs"))
      .finally(() => setLoading(false));
  }, [authHeaders, missionId, navigate]);

  useEffect(() => {
    loadRuns();
  }, [loadRuns]);

  async function confirmDeleteRun() {
    const auth = authHeaders();
    if (!auth) {
      void navigate({ to: "/login" });
      return;
    }
    if (!deleteTarget || deleting) return;

    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteScrapingRun(auth, deleteTarget.id);
      setRuns((currentRuns) => currentRuns.filter((run) => run.id !== deleteTarget.id));
      setDeleteTarget(null);
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : "Failed to delete run");
    } finally {
      setDeleting(false);
    }
  }

  return (
    <AppShell>
      <div className="mx-auto max-w-5xl px-6 py-10">
        <PageHeader
          eyebrow="Scraping Council"
          title="AI Scraping Team Runs"
          description="Historical team plans created from approved blueprints."
          action={
            <Link
              to="/scraping/$missionId"
              params={{ missionId }}
              className="rounded-xl border border-border px-4 py-2.5 text-sm font-medium"
            >
              Mission
            </Link>
          }
        />
        {loading && (
          <GlassCard className="mt-8 p-8 text-sm text-muted-foreground">Loading runs...</GlassCard>
        )}
        {error && <GlassCard className="mt-8 p-8 text-sm text-destructive">{error}</GlassCard>}
        {deleteError && !error && (
          <GlassCard className="mt-8 p-4 text-sm text-destructive">{deleteError}</GlassCard>
        )}
        {!loading && !error && runs.length === 0 && (
          <GlassCard className="mt-8 p-12 text-center text-sm text-muted-foreground">
            No AI scraping team plans have been created yet.
          </GlassCard>
        )}
        {!loading && !error && runs.length > 0 && (
          <div className="mt-8 space-y-3">
            {runs.map((run) => (
              <GlassCard key={run.id} className="p-5 transition hover:border-primary/30">
                <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
                  <Link
                    to="/scraping/$missionId/runs/$runId"
                    params={{ missionId, runId: run.id }}
                    className="min-w-0 flex-1"
                  >
                    <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
                      <div>
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-medium">
                            Run from blueprint v{run.blueprint_version ?? "unknown"}
                          </span>
                          <Badge variant="secondary">{run.status}</Badge>
                        </div>
                        <div className="mt-2 text-sm text-muted-foreground">
                          Created {new Date(run.created_at).toLocaleString()}
                        </div>
                      </div>
                      <div className="grid gap-1 text-sm text-muted-foreground md:text-right">
                        <span>{run.recommended_agent_count ?? "-"} planned agents</span>
                        <span>{run.planner_model_id ?? "Planner pending"}</span>
                      </div>
                    </div>
                  </Link>
                  {isDeletableRunStatus(run.status) && (
                    <div className="md:pl-4">
                      <Button
                        type="button"
                        variant="destructive"
                        disabled={deleting}
                        onClick={() => {
                          setDeleteError(null);
                          setDeleteTarget(run);
                        }}
                      >
                        Delete Run
                      </Button>
                    </div>
                  )}
                </div>
              </GlassCard>
            ))}
          </div>
        )}
      </div>
      <Modal
        open={deleteTarget !== null}
        onClose={deleting ? () => undefined : () => setDeleteTarget(null)}
        title="Delete Run"
        size="md"
      >
        <div className="space-y-4">
          <p className="text-sm text-muted-foreground">
            Delete this run? This permanently deletes the saved AI team plan and all planned agents.
            This action cannot be undone.
          </p>
          {deleteError && <p className="text-sm text-destructive">{deleteError}</p>}
          <div className="flex justify-end gap-2">
            <Button
              type="button"
              variant="outline"
              disabled={deleting}
              onClick={() => setDeleteTarget(null)}
            >
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={deleting}
              onClick={() => void confirmDeleteRun()}
            >
              {deleting ? "Deleting..." : "Delete Run"}
            </Button>
          </div>
        </div>
      </Modal>
    </AppShell>
  );
}

function isDeletableRunStatus(status: ScrapingRunStatus): status is DeletableScrapingRunStatus {
  return DELETABLE_STATUSES.has(status);
}
