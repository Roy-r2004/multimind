import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { GlassCard, PageHeader } from "@/components/cinematic/PageChrome";
import { Modal } from "@/components/Modal";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";
import {
  cancelScrapingExecution,
  deleteScrapingExecution,
  downloadScrapingExecutionWorkbook,
  getScrapingExecution,
  listScrapingExecutionFacilities,
  listScrapingExecutionCoverage,
  listScrapingExecutionEvents,
  listScrapingSourceCandidates,
  listScrapingSourceDiscoveryQueries,
  listScrapingSourceDocuments,
  listScrapingSourceRetrievalAttempts,
  listScrapingExecutionTasks,
  scrapingExecutionStreamUrl,
} from "@/lib/scraping/api";
import type {
  ScrapingCoverageCell,
  ScrapingEvent,
  ScrapingExecutionDetail,
  ScrapingFacilitySummary,
  ScrapingTask,
  SourceCandidate,
  SourceDiscoveryQuery,
  SourceDocument,
  SourceRetrievalAttempt,
} from "@/lib/scraping/types";

export const Route = createFileRoute("/scraping/$missionId/executions/$executionId")({
  head: () => ({ meta: [{ title: "Real Source Discovery Campaign - MultiAI" }] }),
  component: ScrapingExecutionPage,
});

function ScrapingExecutionPage() {
  const { missionId, executionId } = Route.useParams();
  const { authHeaders } = useAuth();
  const navigate = useNavigate();
  const [detail, setDetail] = useState<ScrapingExecutionDetail | null>(null);
  const [tasks, setTasks] = useState<ScrapingTask[]>([]);
  const [coverage, setCoverage] = useState<ScrapingCoverageCell[]>([]);
  const [events, setEvents] = useState<ScrapingEvent[]>([]);
  const [facilities, setFacilities] = useState<ScrapingFacilitySummary[]>([]);
  const [sourceCandidates, setSourceCandidates] = useState<SourceCandidate[]>([]);
  const [discoveryQueries, setDiscoveryQueries] = useState<SourceDiscoveryQuery[]>([]);
  const [retrievalAttempts, setRetrievalAttempts] = useState<SourceRetrievalAttempt[]>([]);
  const [sourceDocuments, setSourceDocuments] = useState<SourceDocument[]>([]);
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const [connectionState, setConnectionState] = useState<"Live" | "Reconnecting" | "Disconnected">(
    "Disconnected",
  );
  const [loading, setLoading] = useState(true);
  const [acting, setActing] = useState(false);
  const [downloadingExcel, setDownloadingExcel] = useState(false);
  const [showDelete, setShowDelete] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lastSequenceRef = useRef(0);
  const refreshTimerRef = useRef<number | null>(null);

  const loadAll = useCallback(async () => {
    const auth = authHeaders();
    if (!auth) {
      void navigate({ to: "/login" });
      return;
    }
    const [
      loadedDetail,
      loadedTasks,
      loadedCoverage,
      loadedEvents,
      loadedFacilities,
      loadedCandidates,
      loadedQueries,
      loadedRetrievalAttempts,
      loadedSourceDocuments,
    ] = await Promise.all([
      getScrapingExecution(auth, executionId),
      listScrapingExecutionTasks(auth, executionId),
      listScrapingExecutionCoverage(auth, executionId),
      listScrapingExecutionEvents(auth, executionId),
      listScrapingExecutionFacilities(auth, executionId),
      listScrapingSourceCandidates(auth, executionId),
      listScrapingSourceDiscoveryQueries(auth, executionId),
      listScrapingSourceRetrievalAttempts(auth, executionId),
      listScrapingSourceDocuments(auth, executionId),
    ]);
    setDetail(loadedDetail);
    setTasks(loadedTasks);
    setCoverage(loadedCoverage);
    setEvents(loadedEvents);
    setFacilities(loadedFacilities);
    setSourceCandidates(loadedCandidates);
    setDiscoveryQueries(loadedQueries);
    setRetrievalAttempts(loadedRetrievalAttempts);
    setSourceDocuments(loadedSourceDocuments);
    lastSequenceRef.current = Math.max(0, ...loadedEvents.map((event) => event.sequence_number));
  }, [authHeaders, executionId, navigate]);

  useEffect(() => {
    setLoading(true);
    setError(null);
    void loadAll()
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load execution"))
      .finally(() => setLoading(false));
  }, [loadAll]);

  useEffect(() => {
    const auth = authHeaders();
    if (!auth) {
      return;
    }
    let cancelled = false;
    let retryMs = 1000;
    let controller: AbortController | null = null;

    async function connect() {
      while (!cancelled) {
        controller = new AbortController();
        try {
          setConnectionState(retryMs === 1000 ? "Live" : "Reconnecting");
          const response = await fetch(
            scrapingExecutionStreamUrl(executionId, lastSequenceRef.current),
            {
              headers: {
                Accept: "text/event-stream",
                Authorization: `Bearer ${auth.token}`,
                "X-Org-Id": auth.orgId,
              },
              credentials: "include",
              signal: controller.signal,
            },
          );
          if (!response.ok || !response.body) {
            throw new Error("Event stream unavailable");
          }
          retryMs = 1000;
          setConnectionState("Live");
          const reader = response.body.getReader();
          const decoder = new TextDecoder();
          let buffer = "";
          while (!cancelled) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const frames = buffer.split("\n\n");
            buffer = frames.pop() ?? "";
            for (const frame of frames) {
              const dataLine = frame
                .split("\n")
                .find((line) => line.startsWith("data: "));
              if (!dataLine) continue;
              const event = JSON.parse(dataLine.slice(6)) as ScrapingEvent;
              if (event.sequence_number <= lastSequenceRef.current) continue;
              lastSequenceRef.current = event.sequence_number;
              setEvents((current) => [...current, event]);
              scheduleRefresh();
              if (["execution_completed", "execution_failed", "execution_cancelled"].includes(event.event_type)) {
                void loadAll();
              }
            }
          }
        } catch {
          if (cancelled) return;
          setConnectionState("Reconnecting");
          await new Promise((resolve) => setTimeout(resolve, retryMs));
          retryMs = Math.min(retryMs * 2, 10000);
        }
      }
    }

    function scheduleRefresh() {
      if (refreshTimerRef.current !== null) return;
      refreshTimerRef.current = window.setTimeout(() => {
        refreshTimerRef.current = null;
        void loadAll().catch(() => undefined);
      }, 1000);
    }

    void connect();
    return () => {
      cancelled = true;
      controller?.abort();
      setConnectionState("Disconnected");
      if (refreshTimerRef.current !== null) {
        window.clearTimeout(refreshTimerRef.current);
      }
    };
  }, [authHeaders, executionId, loadAll]);

  const execution = detail?.execution;
  const filteredEvents = selectedAgentId
    ? events.filter((event) => event.execution_agent_id === selectedAgentId)
    : events;
  const filteredTasks = selectedAgentId
    ? tasks.filter((task) => task.execution_agent_id === selectedAgentId)
    : tasks;
  const filteredCoverage = selectedAgentId
    ? coverage.filter((cell) => cell.assigned_execution_agent_id === selectedAgentId)
    : coverage;
  const completedCoverage = coverage.filter((cell) =>
    ["covered", "covered_no_results", "partially_covered"].includes(cell.status),
  ).length;
  const activeAgents = detail?.agents.filter((agent) => agent.status === "running").length ?? 0;
  const isTerminal = execution
    ? ["completed", "failed", "cancelled"].includes(execution.status)
    : false;
  const uniqueDomainCount = useMemo(
    () => new Set(sourceCandidates.map((candidate) => candidate.domain)).size,
    [sourceCandidates],
  );
  const failedQueryCount = discoveryQueries.filter((query) => query.status === "failed").length;
  const selectedRetrievalCount = tasks.filter((task) => task.task_type === "retrieve_source").length;
  const successfulRetrievalCount = retrievalAttempts.filter((attempt) => attempt.status === "succeeded").length;
  const blockedRetrievalCount = retrievalAttempts.filter((attempt) => attempt.status === "blocked_by_robots").length;
  const unsupportedRetrievalCount = retrievalAttempts.filter(
    (attempt) => attempt.status === "unsupported_content_type",
  ).length;
  const failedRetrievalCount = retrievalAttempts.filter(
    (attempt) => !["succeeded", "blocked_by_robots", "unsupported_content_type"].includes(attempt.status),
  ).length;
  const downloadedBytes = sourceDocuments.reduce((total, document) => total + document.byte_size, 0);
  const candidatesById = useMemo(
    () => new Map(sourceCandidates.map((candidate) => [candidate.id, candidate])),
    [sourceCandidates],
  );
  const summaryCounts = useMemo(() => {
    const count = (status: string) => tasks.filter((task) => task.status === status).length;
    return {
      queued: count("queued"),
      running: count("running"),
      completed: count("completed"),
      failed: count("failed"),
    };
  }, [tasks]);

  async function handleCancel() {
    const auth = authHeaders();
    if (!auth) return;
    setActing(true);
    setError(null);
    try {
      await cancelScrapingExecution(auth, executionId);
      await loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to cancel execution");
    } finally {
      setActing(false);
    }
  }

  async function handleDelete() {
    const auth = authHeaders();
    if (!auth) return;
    setActing(true);
    setError(null);
    try {
      await deleteScrapingExecution(auth, executionId);
      void navigate({ to: "/scraping/$missionId/runs", params: { missionId } });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete execution");
    } finally {
      setActing(false);
      setShowDelete(false);
    }
  }

  async function handleDownloadExcel() {
    const auth = authHeaders();
    if (!auth || downloadingExcel) return;
    setDownloadingExcel(true);
    setError(null);
    try {
      const { blob, filename } = await downloadScrapingExecutionWorkbook(auth, executionId);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to download Excel report");
    } finally {
      setDownloadingExcel(false);
    }
  }

  return (
    <AppShell>
      <div className="mx-auto max-w-7xl px-6 py-10">
        <PageHeader
          eyebrow="Execution Campaigns"
          title="Real Source Retrieval Campaign"
          description="This phase discovers real candidate sources and retrieves a bounded set of secure source pages. Facility extraction is not yet enabled."
          action={
            <Link
              to="/scraping/$missionId/runs/$runId"
              params={{ missionId, runId: execution?.team_plan_id ?? "" }}
              className="rounded-xl border border-border px-4 py-2.5 text-sm font-medium"
            >
              AI Team Plan
            </Link>
          }
        />
        {loading && <GlassCard className="mt-8 p-8 text-sm">Loading execution...</GlassCard>}
        {error && <GlassCard className="mt-8 p-8 text-sm text-destructive">{error}</GlassCard>}
        {detail && execution && (
          <div className="mt-8 space-y-5">
            <GlassCard className="border-emerald-500/40 bg-emerald-500/10 p-5">
              <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                <div>
                  <p className="font-semibold">Real source pages have been retrieved and stored</p>
                  <p className="text-sm text-muted-foreground">
                    Facility extraction and verification are not yet enabled.
                  </p>
                </div>
                <Badge variant="secondary">{connectionState}</Badge>
              </div>
            </GlassCard>
            <GlassCard className="p-6">
              <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant="secondary">{execution.status_label}</Badge>
                    <Badge variant="outline">{execution.mode}</Badge>
                    <span className="text-sm text-muted-foreground">
                      {execution.country_name} ({execution.country_code})
                    </span>
                  </div>
                  <h2 className="mt-3 text-xl font-semibold">{execution.execution_type}</h2>
                  <p className="mt-1 text-sm text-muted-foreground">
                    Started {execution.started_at ? new Date(execution.started_at).toLocaleString() : "not yet"} ·
                    Completed {execution.completed_at ? new Date(execution.completed_at).toLocaleString() : "not yet"}
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    disabled={!isTerminal || downloadingExcel || facilities.length === 0}
                    onClick={() => void handleDownloadExcel()}
                  >
                    {downloadingExcel ? "Preparing Excel…" : "Download Excel Report"}
                  </Button>
                  {detail.can_cancel && (
                    <Button type="button" disabled={acting} onClick={() => void handleCancel()}>
                      {acting ? "Cancelling..." : "Cancel Execution"}
                    </Button>
                  )}
                  {detail.can_delete && (
                    <Button
                      type="button"
                      variant="destructive"
                      disabled={acting}
                      onClick={() => setShowDelete(true)}
                    >
                      Delete Execution
                    </Button>
                  )}
                </div>
              </div>
              <div className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <Metric label="Active agents" value={activeAgents} />
                <Metric label="Tasks queued/running/completed/failed" value={`${summaryCounts.queued}/${summaryCounts.running}/${summaryCounts.completed}/${summaryCounts.failed}`} />
                <Metric label="Coverage cells" value={`${completedCoverage}/${coverage.length}`} />
                <Metric label="Coverage debt" value={execution.coverage_debt} />
                <Metric label="Source candidates" value={sourceCandidates.length} />
                <Metric label="Selected retrievals" value={selectedRetrievalCount} />
                <Metric label="Retrieval attempts" value={retrievalAttempts.length} />
                <Metric label="Successful retrievals" value={successfulRetrievalCount} />
                <Metric label="Successful documents" value={sourceDocuments.length} />
                <Metric label="Blocked retrievals" value={blockedRetrievalCount} />
                <Metric label="Unsupported types" value={unsupportedRetrievalCount} />
                <Metric label="Failed retrievals" value={failedRetrievalCount} />
                <Metric label="Downloaded bytes" value={downloadedBytes.toLocaleString()} />
                <Metric label="Unique domains" value={uniqueDomainCount} />
                <Metric label="Discovery queries" value={discoveryQueries.length} />
                <Metric label="Query failures" value={failedQueryCount} />
                <Metric label="Facilities extracted" value={facilities.length} />
              </div>
              {!isTerminal && facilities.length > 0 && (
                <p className="mt-4 text-sm text-muted-foreground">
                  Excel report available after execution finishes.
                </p>
              )}
            </GlassCard>
            <GlassCard className="p-6">
              <div className="mb-4 flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
                <div>
                  <h2 className="text-lg font-semibold">Candidate Sources</h2>
                  <p className="text-sm text-muted-foreground">
                    Real HTTP/HTTPS candidates discovered by the configured search provider.
                  </p>
                </div>
                <Badge variant="secondary">{sourceCandidates.length} candidates</Badge>
              </div>
              {sourceCandidates.length === 0 ? (
                <div className="rounded-lg border border-border p-4 text-sm text-muted-foreground">
                  No source candidates have been persisted for this execution yet.
                </div>
              ) : (
                <div className="overflow-auto rounded-lg border border-border">
                  <table className="w-full min-w-[980px] text-left text-sm">
                    <thead className="bg-muted/50">
                      <tr>
                        <th className="px-3 py-2 font-medium">Source</th>
                        <th className="px-3 py-2 font-medium">Domain</th>
                        <th className="px-3 py-2 font-medium">Category</th>
                        <th className="px-3 py-2 font-medium">Region</th>
                        <th className="px-3 py-2 font-medium">Language</th>
                        <th className="px-3 py-2 font-medium">Provider</th>
                        <th className="px-3 py-2 font-medium">Rank</th>
                        <th className="px-3 py-2 font-medium">Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {sourceCandidates.map((candidate) => (
                        <tr key={candidate.id} className="border-t border-border">
                          <td className="px-3 py-2 align-top">
                            <a
                              href={candidate.canonical_url}
                              target="_blank"
                              rel="noreferrer"
                              className="font-medium text-primary underline-offset-4 hover:underline"
                            >
                              {candidate.title || candidate.canonical_url}
                            </a>
                            <div className="mt-1 max-w-xl truncate text-xs text-muted-foreground">
                              {candidate.canonical_url}
                            </div>
                          </td>
                          <td className="px-3 py-2 align-top">{candidate.domain}</td>
                          <td className="px-3 py-2 align-top">{candidate.source_category}</td>
                          <td className="px-3 py-2 align-top">{candidate.region_name}</td>
                          <td className="px-3 py-2 align-top">{candidate.language_name}</td>
                          <td className="px-3 py-2 align-top">{candidate.provider}</td>
                          <td className="px-3 py-2 align-top">{candidate.rank}</td>
                          <td className="px-3 py-2 align-top">{candidate.status}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </GlassCard>
            <GlassCard className="p-6">
              <div className="mb-4 flex items-center justify-between">
                <h2 className="text-lg font-semibold">Agents</h2>
                {selectedAgentId && (
                  <Button type="button" variant="outline" onClick={() => setSelectedAgentId(null)}>
                    Clear filter
                  </Button>
                )}
              </div>
              <div className="grid gap-4 lg:grid-cols-2">
                {detail.agents.map((agent) => (
                  <button
                    key={agent.id}
                    type="button"
                    onClick={() => setSelectedAgentId(agent.id)}
                    className="rounded-lg border border-border p-4 text-left hover:bg-muted/40"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant="secondary">{agent.status}</Badge>
                      <span className="font-medium">{agent.planned_agent_name}</span>
                    </div>
                    <p className="mt-1 text-sm text-muted-foreground">
                      {agent.planned_agent_role} · {agent.model_id}
                    </p>
                    <p className="mt-2 text-sm">{agent.current_action ?? "No current action"}</p>
                    {agent.error_message && (
                      <p className="mt-2 text-sm text-destructive">{agent.error_message}</p>
                    )}
                  </button>
                ))}
              </div>
            </GlassCard>
            <GridSection title="Coverage" rows={filteredCoverage.map(formatCoverageRow)} />
            <GridSection title="Tasks" rows={filteredTasks.map(formatTaskRow)} />
            <GridSection title="Discovery Queries" rows={discoveryQueries.map(formatQueryRow)} />
            <GridSection
              title="Retrieval Attempts"
              rows={retrievalAttempts.map((attempt) => formatAttemptRow(attempt, candidatesById))}
            />
            <GridSection
              title="Source Documents"
              rows={sourceDocuments.map((document) => formatDocumentRow(document))}
            />
            <GlassCard className="p-6">
              <h2 className="text-lg font-semibold">Live Activity</h2>
              <div className="mt-4 max-h-[520px] space-y-3 overflow-auto">
                {filteredEvents.map((event) => (
                  <div key={event.id} className="rounded-lg border border-border p-3 text-sm">
                    <span className="text-muted-foreground">
                      {new Date(event.created_at).toLocaleTimeString()} -
                    </span>{" "}
                    <span className="font-medium">{event.event_type}</span>
                    <p className="mt-1 text-muted-foreground">{event.message}</p>
                  </div>
                ))}
              </div>
            </GlassCard>
          </div>
        )}
      </div>
      <Modal
        open={showDelete}
        onClose={acting ? () => undefined : () => setShowDelete(false)}
        title="Delete Execution"
        size="md"
      >
        <div className="space-y-4">
          <p className="text-sm text-muted-foreground">
            Delete this terminal source discovery execution campaign? This permanently deletes tasks, coverage
            history, and event history. This action cannot be undone.
          </p>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" disabled={acting} onClick={() => setShowDelete(false)}>
              Cancel
            </Button>
            <Button type="button" variant="destructive" disabled={acting} onClick={() => void handleDelete()}>
              {acting ? "Deleting..." : "Delete Execution"}
            </Button>
          </div>
        </div>
      </Modal>
    </AppShell>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg border border-border p-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 text-lg font-semibold">{value}</p>
    </div>
  );
}

function GridSection({ title, rows }: { title: string; rows: string[][] }) {
  return (
    <GlassCard className="p-6">
      <h2 className="text-lg font-semibold">{title}</h2>
      <div className="mt-4 overflow-auto rounded-lg border border-border">
        <table className="w-full min-w-[760px] text-left text-sm">
          <tbody>
            {rows.map((row, index) => (
              <tr key={`${title}-${index}`} className="border-b border-border last:border-0">
                {row.map((cell, cellIndex) => (
                  <td key={cellIndex} className="px-3 py-2 align-top">
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </GlassCard>
  );
}

function formatCoverageRow(cell: ScrapingCoverageCell) {
  return [
    cell.region_name,
    cell.language_name,
    cell.source_category,
    cell.status,
    `${cell.result_count} results`,
    cell.assigned_agent_name ?? "Unassigned",
    cell.reason ?? "",
  ];
}

function formatTaskRow(task: ScrapingTask) {
  return [
    task.title,
    task.task_type,
    task.agent_name ?? "Unassigned",
    task.coverage_label ?? "",
    task.status,
    task.current_action ?? "",
    `${task.attempt_count}/${task.max_attempts}`,
  ];
}

function formatQueryRow(query: SourceDiscoveryQuery) {
  return [
    query.query_text,
    query.provider,
    query.status,
    `${query.result_count} candidates`,
    query.region_name ?? "",
    query.language_name,
    query.source_category,
    query.error_code ?? "",
  ];
}

function formatAttemptRow(
  attempt: SourceRetrievalAttempt,
  candidatesById: Map<string, SourceCandidate>,
) {
  const candidate = candidatesById.get(attempt.source_candidate_id);
  return [
    candidate?.title || candidate?.canonical_url || attempt.source_candidate_id,
    safeHostname(attempt.final_url ?? attempt.requested_url),
    attempt.status,
    attempt.http_status == null ? "" : String(attempt.http_status),
    attempt.content_type ?? "",
    attempt.bytes_received == null ? "" : attempt.bytes_received.toLocaleString(),
    String(attempt.redirect_count),
    attempt.robots_status ?? "",
    attempt.failure_classification ?? "",
  ];
}

function formatDocumentRow(document: SourceDocument) {
  return [
    safeHostname(document.final_url),
    document.final_url,
    document.content_type,
    document.byte_size.toLocaleString(),
    new Date(document.retrieval_timestamp).toLocaleString(),
    document.content_sha256.slice(0, 12),
  ];
}

function safeHostname(value?: string | null) {
  if (!value) return "";
  try {
    return new URL(value).hostname;
  } catch {
    return "";
  }
}
