import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { GlassCard, PageHeader } from "@/components/cinematic/PageChrome";
import { Modal } from "@/components/Modal";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ApiClientError } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";
import {
  cancelScrapingRun,
  createScrapingExecution,
  deleteScrapingExecution,
  deleteScrapingRun,
  getScrapingRun,
  listScrapingExecutions,
} from "@/lib/scraping/api";
import type {
  DeletableScrapingRunStatus,
  ScrapingExecutionConflictDetails,
  ScrapingExecutionSummary,
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
  const [executions, setExecutions] = useState<ScrapingExecutionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [startingExecution, setStartingExecution] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deletingExecutionId, setDeletingExecutionId] = useState<string | null>(null);
  const [showDelete, setShowDelete] = useState(false);
  const [executionToDelete, setExecutionToDelete] = useState<ScrapingExecutionSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadRun = useCallback(() => {
    const auth = authHeaders();
    if (!auth) {
      void navigate({ to: "/login" });
      return;
    }
    setLoading(true);
    setError(null);
    void Promise.all([getScrapingRun(auth, runId), listScrapingExecutions(auth, runId)])
      .then(([loadedRun, loadedExecutions]) => {
        setRun(loadedRun);
        setExecutions(loadedExecutions);
      })
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
  const activeExecution = executions.find((execution) =>
    ["queued", "running", "cancel_requested"].includes(execution.status),
  );

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

  async function handleStartExecution() {
    const auth = authHeaders();
    if (!auth) {
      void navigate({ to: "/login" });
      return;
    }
    if (activeExecution) {
      void navigate({
        to: "/scraping/$missionId/executions/$executionId",
        params: { missionId, executionId: activeExecution.id },
      });
      return;
    }
    setStartingExecution(true);
    setError(null);
    try {
      const execution = await createScrapingExecution(auth, runId);
      setExecutions((current) => [execution, ...current]);
      void navigate({
        to: "/scraping/$missionId/executions/$executionId",
        params: { missionId, executionId: execution.id },
      });
    } catch (err) {
      if (err instanceof ApiClientError && err.status === 409) {
        const details = err.body?.details as ScrapingExecutionConflictDetails | undefined;
        if (details?.existing_execution_id) {
          void navigate({
            to: "/scraping/$missionId/executions/$executionId",
            params: { missionId, executionId: details.existing_execution_id },
          });
          return;
        }
      }
      setError(err instanceof Error ? err.message : "Failed to start source discovery execution");
    } finally {
      setStartingExecution(false);
    }
  }

  async function handleDeleteExecution() {
    const auth = authHeaders();
    if (!auth || !executionToDelete) {
      return;
    }
    setDeletingExecutionId(executionToDelete.id);
    setError(null);
    try {
      await deleteScrapingExecution(auth, executionToDelete.id);
      setExecutions((current) =>
        current.filter((execution) => execution.id !== executionToDelete.id),
      );
      setExecutionToDelete(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete execution");
    } finally {
      setDeletingExecutionId(null);
    }
  }

  return (
    <AppShell>
      <div className="mx-auto max-w-6xl px-6 py-10">
        <PageHeader
          eyebrow="Scraping Council"
          title={run ? `AI Team Plan for ${run.mission_title}` : "AI Team Plan"}
          description="Saved AI team plan and real source discovery campaigns."
          action={
            <Link
              to="/scraping/$missionId/runs"
              params={{ missionId }}
              className="rounded-xl border border-border px-4 py-2.5 text-sm font-medium"
            >
              AI Team Plans
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
                    {deleting ? "Deleting..." : "Delete AI Team Plan"}
                  </Button>
                </div>
              </div>
            </GlassCard>
            <GlassCard className="p-6">
              <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
                <div>
                  <h2 className="text-lg font-semibold">Execution Campaigns</h2>
                  <p className="mt-1 text-sm text-muted-foreground">
                    This phase discovers and stores real candidate sources. Website retrieval and facility extraction are not yet enabled.
                  </p>
                </div>
                <Button
                  type="button"
                  disabled={startingExecution || run.status !== "planned"}
                  onClick={() => void handleStartExecution()}
                >
                  {startingExecution
                    ? "Starting Source Discovery..."
                    : activeExecution
                      ? "View Active Source Discovery"
                      : "Start Source Discovery"}
                </Button>
              </div>
              {executions.length === 0 ? (
                <p className="mt-5 text-sm text-muted-foreground">
                  No execution campaigns have been started for this AI team plan.
                </p>
              ) : (
                <div className="mt-5 divide-y divide-border rounded-lg border border-border">
                  {executions.map((execution) => (
                    <div
                      key={execution.id}
                      className="flex flex-col gap-3 p-4 md:flex-row md:items-center md:justify-between"
                    >
                      <Link
                        to="/scraping/$missionId/executions/$executionId"
                        params={{ missionId, executionId: execution.id }}
                        className="min-w-0 flex-1"
                      >
                        <div className="flex flex-wrap items-center gap-2">
                          <Badge variant="secondary">{execution.status}</Badge>
                          <span className="font-medium">{execution.execution_type}</span>
                          <span className="text-sm text-muted-foreground">
                            {execution.country_name} ({execution.country_code})
                          </span>
                        </div>
                        <p className="mt-1 text-sm text-muted-foreground">
                          {execution.mode} · {execution.sources_discovered} candidate sources ·{" "}
                          {execution.coverage_debt} coverage debt ·{" "}
                          {new Date(execution.created_at).toLocaleString()}
                        </p>
                      </Link>
                      {["completed", "failed", "cancelled"].includes(execution.status) && (
                        <Button
                          type="button"
                          variant="outline"
                          disabled={deletingExecutionId === execution.id}
                          onClick={() => setExecutionToDelete(execution)}
                        >
                          {deletingExecutionId === execution.id
                            ? "Deleting..."
                            : "Delete Execution"}
                        </Button>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </GlassCard>
            {run.agents.length === 0 ? (
              <GlassCard className="p-8 text-sm text-muted-foreground">
                No agents were saved for this AI team plan.
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
            Delete this AI team plan? This permanently deletes its planned agents, terminal
            execution campaigns, tasks, coverage history, and event history. This action cannot be
            undone.
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
      <Modal
        open={executionToDelete !== null}
        onClose={deletingExecutionId ? () => undefined : () => setExecutionToDelete(null)}
        title="Delete Execution Campaign"
        size="md"
      >
        <div className="space-y-4">
          <p className="text-sm text-muted-foreground">
            Delete this terminal source discovery execution campaign? This permanently deletes its tasks,
            coverage history, and event history. This action cannot be undone.
          </p>
          <div className="flex justify-end gap-2">
            <Button
              type="button"
              variant="outline"
              disabled={deletingExecutionId !== null}
              onClick={() => setExecutionToDelete(null)}
            >
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={deletingExecutionId !== null}
              onClick={() => void handleDeleteExecution()}
            >
              {deletingExecutionId ? "Deleting..." : "Delete Execution"}
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
