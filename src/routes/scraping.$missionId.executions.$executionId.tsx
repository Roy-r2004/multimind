import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { GlassCard, PageHeader } from "@/components/cinematic/PageChrome";
import { Modal } from "@/components/Modal";
import { LiveSiteActivity } from "@/components/scraping/LiveSiteActivity";
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
          eyebrow="Scrape results"
          title={
            facilities.length > 0
              ? `${facilities.length} facilities found`
              : isTerminal
                ? "Scrape finished"
                : "Scrape in progress"
          }
          description={`${execution?.country_name ?? "Country"} · ${sourceDocuments.length} pages downloaded · ${sourceCandidates.length} sources`}
          action={
            <Link
              to="/scraping/$missionId"
              params={{ missionId }}
              className="rounded-xl border border-border px-4 py-2.5 text-sm font-medium"
            >
              Back to mission
            </Link>
          }
        />
        {loading && <GlassCard className="mt-8 p-8 text-sm">Loading results...</GlassCard>}
        {error && <GlassCard className="mt-8 p-8 text-sm text-destructive">{error}</GlassCard>}
        {detail && execution && (
          <div className="mt-8 space-y-5">
            <GlassCard className="p-5">
              <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="secondary">{execution.status_label}</Badge>
                  <span className="text-sm text-muted-foreground">{connectionState}</span>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    disabled={!isTerminal || downloadingExcel || facilities.length === 0}
                    onClick={() => void handleDownloadExcel()}
                  >
                    {downloadingExcel ? "Preparing Excel…" : "Download Excel"}
                  </Button>
                  {detail.can_cancel && (
                    <Button type="button" variant="outline" disabled={acting} onClick={() => void handleCancel()}>
                      {acting ? "Cancelling..." : "Cancel"}
                    </Button>
                  )}
                </div>
              </div>
              <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <Metric label="Facilities" value={facilities.length} />
                <Metric label="Pages" value={sourceDocuments.length} />
                <Metric label="Sources" value={sourceCandidates.length} />
                <Metric label="Duplicates" value={execution.duplicates_detected} />
              </div>
            </GlassCard>

            <LiveSiteActivity
              candidates={sourceCandidates}
              tasks={tasks}
              attempts={retrievalAttempts}
              documents={sourceDocuments}
              events={events}
              isTerminal={isTerminal}
            />

            <GlassCard className="p-6">
              <div className="mb-4 flex items-center justify-between">
                <h2 className="text-xl font-semibold">Facilities</h2>
                <Badge variant="secondary">{facilities.length}</Badge>
              </div>
              {facilities.length === 0 ? (
                <div className="rounded-lg border border-dashed border-border p-6 text-sm text-muted-foreground">
                  {isTerminal
                    ? "No facilities were published from the pages we retrieved."
                    : "Still running… facilities show up here when extraction finishes."}
                </div>
              ) : (
                <div className="overflow-auto rounded-lg border border-border">
                  <table className="min-w-full text-left text-sm">
                    <thead className="bg-muted/40 text-xs uppercase text-muted-foreground">
                      <tr>
                        <th className="px-3 py-2">Name</th>
                        <th className="px-3 py-2">Type</th>
                        <th className="px-3 py-2">Website</th>
                        <th className="px-3 py-2">Confidence</th>
                      </tr>
                    </thead>
                    <tbody>
                      {facilities.map((facility) => (
                        <tr key={facility.id} className="border-t border-border">
                          <td className="px-3 py-2 align-top font-medium">
                            {facility.canonical_name}
                            {facility.primary_city || facility.primary_region ? (
                              <p className="text-xs text-muted-foreground">
                                {[facility.primary_city, facility.primary_region]
                                  .filter(Boolean)
                                  .join(", ")}
                              </p>
                            ) : null}
                          </td>
                          <td className="px-3 py-2 align-top">{facility.facility_type}</td>
                          <td className="px-3 py-2 align-top">
                            {facility.primary_website ? (
                              <a
                                href={facility.primary_website}
                                target="_blank"
                                rel="noreferrer"
                                className="text-primary underline-offset-2 hover:underline"
                              >
                                Open site
                              </a>
                            ) : (
                              "—"
                            )}
                          </td>
                          <td className="px-3 py-2 align-top">
                            {(facility.confidence_score * 100).toFixed(0)}%
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </GlassCard>

            <details className="rounded-xl border border-border bg-card/40 p-4">
              <summary className="cursor-pointer text-sm font-medium">
                Sources & pages ({sourceCandidates.length} sources · {sourceDocuments.length} pages)
              </summary>
              <div className="mt-4 space-y-4">
                <div className="overflow-auto rounded-lg border border-border">
                  <table className="w-full min-w-[720px] text-left text-sm">
                    <thead className="bg-muted/50">
                      <tr>
                        <th className="px-3 py-2 font-medium">Source</th>
                        <th className="px-3 py-2 font-medium">Domain</th>
                        <th className="px-3 py-2 font-medium">Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {sourceCandidates.slice(0, 40).map((candidate) => (
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
                          </td>
                          <td className="px-3 py-2 align-top">{candidate.domain}</td>
                          <td className="px-3 py-2 align-top">{candidate.status}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {sourceCandidates.length > 40 && (
                  <p className="text-xs text-muted-foreground">
                    Showing first 40 of {sourceCandidates.length} sources.
                  </p>
                )}
              </div>
            </details>

            <details className="rounded-xl border border-border bg-card/40 p-4">
              <summary className="cursor-pointer text-sm font-medium">
                Technical details (agents, tasks, logs)
              </summary>
              <div className="mt-4 space-y-4">
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                  <Metric label="Active agents" value={activeAgents} />
                  <Metric
                    label="Tasks done"
                    value={`${summaryCounts.completed}/${summaryCounts.queued + summaryCounts.running + summaryCounts.completed + summaryCounts.failed}`}
                  />
                  <Metric label="Coverage" value={`${completedCoverage}/${coverage.length}`} />
                  <Metric label="Blocked" value={blockedRetrievalCount} />
                </div>
                <GlassCard className="p-4">
                  <div className="mb-3 flex items-center justify-between">
                    <h3 className="font-medium">Agents</h3>
                    {selectedAgentId && (
                      <Button type="button" variant="outline" size="sm" onClick={() => setSelectedAgentId(null)}>
                        Clear filter
                      </Button>
                    )}
                  </div>
                  <div className="grid gap-3 lg:grid-cols-2">
                    {detail.agents.map((agent) => (
                      <button
                        key={agent.id}
                        type="button"
                        onClick={() => setSelectedAgentId(agent.id)}
                        className="rounded-lg border border-border p-3 text-left text-sm hover:bg-muted/40"
                      >
                        <div className="flex flex-wrap items-center gap-2">
                          <Badge variant="secondary">{agent.status}</Badge>
                          <span className="font-medium">{agent.planned_agent_name}</span>
                        </div>
                        <p className="mt-1 text-muted-foreground">{agent.current_action ?? "Idle"}</p>
                      </button>
                    ))}
                  </div>
                </GlassCard>
                <GridSection title="Tasks" rows={filteredTasks.map(formatTaskRow)} />
                <GridSection title="Coverage" rows={filteredCoverage.map(formatCoverageRow)} />
                <GridSection title="Discovery Queries" rows={discoveryQueries.map(formatQueryRow)} />
                <GridSection
                  title="Retrieval Attempts"
                  rows={retrievalAttempts.map((attempt) => formatAttemptRow(attempt, candidatesById))}
                />
                <GridSection
                  title="Source Documents"
                  rows={sourceDocuments.map((document) => formatDocumentRow(document))}
                />
                <GlassCard className="p-4">
                  <h3 className="font-medium">Live activity</h3>
                  <div className="mt-3 max-h-[360px] space-y-2 overflow-auto">
                    {filteredEvents.map((event) => (
                      <div key={event.id} className="rounded-lg border border-border p-2 text-sm">
                        <span className="text-muted-foreground">
                          {new Date(event.created_at).toLocaleTimeString()}
                        </span>{" "}
                        <span className="font-medium">{event.event_type}</span>
                        <p className="text-muted-foreground">{event.message}</p>
                      </div>
                    ))}
                  </div>
                </GlassCard>
                {detail.can_delete && (
                  <Button type="button" variant="destructive" disabled={acting} onClick={() => setShowDelete(true)}>
                    Delete scrape
                  </Button>
                )}
              </div>
            </details>
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
