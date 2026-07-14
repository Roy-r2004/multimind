import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { GlassCard, PageHeader } from "@/components/cinematic/PageChrome";
import { Modal } from "@/components/Modal";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";
import { cancelScrapingRun, deleteScrapingRun, getScrapingRun } from "@/lib/scraping/api";
import type {
  DeletableScrapingRunStatus,
  ScrapingRunAgent,
  ScrapingRunDetail,
  ScrapingRunStatus,
} from "@/lib/scraping/types";

const DELETABLE_STATUSES = new Set<ScrapingRunStatus>([
  "planned",
  "completed",
  "failed",
  "cancelled",
]);

export const Route = createFileRoute("/scraping/$missionId/runs/$runId")({
  head: () => ({ meta: [{ title: "Scraping Run Detail - MultiAI" }] }),
  component: ScrapingRunDetailPage,
});

function ScrapingRunDetailPage() {
  const { missionId, runId } = Route.useParams();
  const { authHeaders } = useAuth();
  const navigate = useNavigate();
  const [run, setRun] = useState<ScrapingRunDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [cancelling, setCancelling] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [showDelete, setShowDelete] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadRun = useCallback(() => {
    const auth = authHeaders();
    if (!auth) {
      void navigate({ to: "/login" });
      return;
    }
    setLoading(true);
    setError(null);
    void getScrapingRun(auth, runId)
      .then(setRun)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load run"))
      .finally(() => setLoading(false));
  }, [authHeaders, navigate, runId]);

  useEffect(() => {
    loadRun();
  }, [loadRun]);

  const agentNameById = useMemo(() => {
    const names = new Map<string, string>();
    for (const agent of run?.agents ?? []) {
      names.set(agent.id, `${agent.sequence}. ${agent.name}`);
    }
    return names;
  }, [run?.agents]);
  const canDelete = run ? isDeletableRunStatus(run.status) : false;

  async function handleCancel() {
    const auth = authHeaders();
    if (!auth) {
      void navigate({ to: "/login" });
      return;
    }
    setCancelling(true);
    setError(null);
    try {
      setRun(await cancelScrapingRun(auth, runId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to cancel run");
    } finally {
      setCancelling(false);
    }
  }

  async function handleDelete() {
    const auth = authHeaders();
    if (!auth) {
      void navigate({ to: "/login" });
      return;
    }
    setDeleting(true);
    setError(null);
    try {
      await deleteScrapingRun(auth, runId);
      void navigate({ to: "/scraping/$missionId/runs", params: { missionId } });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete run");
      setShowDelete(false);
    } finally {
      setDeleting(false);
    }
  }

  return (
    <AppShell>
      <div className="mx-auto max-w-6xl px-6 py-10">
        <PageHeader
          eyebrow="Scraping Council"
          title={run ? `Team plan for ${run.mission_title}` : "AI Scraping Team Plan"}
          description="Planned AI agents for a future execution phase. No websites are being scraped."
          action={
            <Link
              to="/scraping/$missionId/runs"
              params={{ missionId }}
              className="rounded-xl border border-border px-4 py-2.5 text-sm font-medium"
            >
              Runs
            </Link>
          }
        />
        {loading && (
          <GlassCard className="mt-8 p-8 text-sm text-muted-foreground">Loading run...</GlassCard>
        )}
        {error && <GlassCard className="mt-8 p-8 text-sm text-destructive">{error}</GlassCard>}
        {run && (
          <div className="mt-8 space-y-5">
            <GlassCard className="p-6">
              <div className="flex flex-col gap-5 md:flex-row md:items-start md:justify-between">
                <div className="space-y-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant="secondary">{run.status}</Badge>
                    <span className="text-sm text-muted-foreground">
                      Blueprint v{run.blueprint_version ?? "unknown"}
                    </span>
                  </div>
                  <h2 className="text-xl font-semibold">
                    Orchestrator selected {run.recommended_agent_count ?? run.agents.length} AI
                    agents
                  </h2>
                  {run.planner_rationale && (
                    <p className="max-w-3xl text-sm text-muted-foreground">
                      {run.planner_rationale}
                    </p>
                  )}
                  {run.error_message && (
                    <p className="text-sm text-destructive">{run.error_message}</p>
                  )}
                </div>
                <div className="grid gap-2 text-sm text-muted-foreground md:text-right">
                  <span>Planner: {run.planner_model_id ?? "Pending"}</span>
                  <span>Created: {new Date(run.created_at).toLocaleString()}</span>
                  {run.completed_at && (
                    <span>Planned: {new Date(run.completed_at).toLocaleString()}</span>
                  )}
                  {run.status === "planned" && (
                    <Button
                      type="button"
                      variant="outline"
                      disabled={cancelling}
                      onClick={() => void handleCancel()}
                    >
                      {cancelling ? "Cancelling..." : "Cancel Plan"}
                    </Button>
                  )}
                  <Button
                    type="button"
                    variant="destructive"
                    disabled={!canDelete || deleting}
                    onClick={() => setShowDelete(true)}
                  >
                    {deleting ? "Deleting..." : "Delete Run"}
                  </Button>
                </div>
              </div>
            </GlassCard>
            {run.agents.length === 0 ? (
              <GlassCard className="p-8 text-sm text-muted-foreground">
                No agents were saved for this run.
              </GlassCard>
            ) : (
              <div className="grid gap-4 lg:grid-cols-2">
                {run.agents.map((agent) => (
                  <AgentCard key={agent.id} agent={agent} agentNameById={agentNameById} />
                ))}
              </div>
            )}
          </div>
        )}
      </div>
      <Modal
        open={showDelete}
        onClose={deleting ? () => undefined : () => setShowDelete(false)}
        title="Delete Run"
        size="md"
      >
        <div className="space-y-4">
          <p className="text-sm text-muted-foreground">
            Permanently delete this scraping run? This also deletes its planned AI agent team and
            cannot be undone.
          </p>
          <div className="flex justify-end gap-2">
            <Button
              type="button"
              variant="outline"
              disabled={deleting}
              onClick={() => setShowDelete(false)}
            >
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={deleting}
              onClick={() => void handleDelete()}
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

function AgentCard({
  agent,
  agentNameById,
}: {
  agent: ScrapingRunAgent;
  agentNameById: Map<string, string>;
}) {
  const dependencies = agent.dependency_agent_ids.map(
    (dependencyId) => agentNameById.get(dependencyId) ?? dependencyId,
  );
  return (
    <GlassCard className="p-5">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="secondary">#{agent.sequence}</Badge>
        <h3 className="font-semibold">{agent.name}</h3>
        <Badge variant="secondary">{agent.status}</Badge>
      </div>
      <dl className="mt-4 space-y-3 text-sm">
        <Field label="Role" value={agent.role} />
        <Field label="Model" value={agent.model_id} />
        <Field label="Purpose" value={agent.purpose} />
        <Field label="Instructions" value={agent.instructions} />
        <Field label="Assigned scope" value={formatScope(agent.assigned_scope)} />
        <Field
          label="Dependencies"
          value={dependencies.length > 0 ? dependencies.join(", ") : "None"}
        />
      </dl>
    </GlassCard>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="font-medium">{label}</dt>
      <dd className="mt-1 whitespace-pre-wrap text-muted-foreground">{value}</dd>
    </div>
  );
}

function formatScope(scope: Record<string, unknown>) {
  if (Object.keys(scope).length === 0) {
    return "No specific scope";
  }
  return JSON.stringify(scope, null, 2);
}
