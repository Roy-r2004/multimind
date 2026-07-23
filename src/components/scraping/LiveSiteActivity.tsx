import { useMemo } from "react";
import { ExternalLink, Globe2, Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { GlassCard } from "@/components/cinematic/PageChrome";
import { cn } from "@/lib/utils";
import type {
  ScrapingEvent,
  ScrapingTask,
  SourceCandidate,
  SourceDocument,
  SourceRetrievalAttempt,
} from "@/lib/scraping/types";

export type SiteStage =
  | "discovered"
  | "queued"
  | "opening"
  | "fetched"
  | "extracting"
  | "extracted"
  | "skipped"
  | "blocked"
  | "unsupported"
  | "failed";

export type SiteActivityRow = {
  key: string;
  title: string;
  url: string;
  domain: string;
  stage: SiteStage;
  detail?: string;
  updatedAt?: string;
};

const STAGE_RANK: Record<SiteStage, number> = {
  opening: 0,
  extracting: 1,
  queued: 2,
  fetched: 3,
  extracted: 4,
  skipped: 5,
  blocked: 6,
  unsupported: 7,
  failed: 8,
  discovered: 9,
};

const STAGE_LABEL: Record<SiteStage, string> = {
  discovered: "Discovered",
  queued: "Queued",
  opening: "Opening",
  fetched: "Fetched",
  extracting: "Extracting",
  extracted: "Extracted",
  skipped: "Skipped",
  blocked: "Blocked",
  unsupported: "Unsupported",
  failed: "Failed",
};

type Props = {
  candidates: SourceCandidate[];
  tasks: ScrapingTask[];
  attempts: SourceRetrievalAttempt[];
  documents: SourceDocument[];
  events: ScrapingEvent[];
  isTerminal: boolean;
};

export function LiveSiteActivity({
  candidates,
  tasks,
  attempts,
  documents,
  events,
  isTerminal,
}: Props) {
  const rows = useMemo(
    () => buildSiteActivityRows({ candidates, tasks, attempts, documents, events }),
    [attempts, candidates, documents, events, tasks],
  );

  const activeCount = rows.filter((row) => row.stage === "opening" || row.stage === "extracting")
    .length;
  const extractedCount = rows.filter((row) => row.stage === "extracted").length;
  const failedCount = rows.filter((row) =>
    ["failed", "blocked", "unsupported", "skipped"].includes(row.stage),
  ).length;

  if (rows.length === 0) {
    return (
      <GlassCard className="p-6">
        <div className="flex items-start gap-3">
          <div className="rounded-lg border border-border bg-muted/30 p-2">
            <Globe2 className="size-4 text-muted-foreground" />
          </div>
          <div>
            <h2 className="text-lg font-semibold">Live websites</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              {isTerminal
                ? "No source websites were opened during this scrape."
                : "Waiting for discovery… URLs will appear here as sites are opened and extracted."}
            </p>
          </div>
        </div>
      </GlassCard>
    );
  }

  return (
    <GlassCard className="p-6">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-semibold">Live websites</h2>
            {!isTerminal && activeCount > 0 && (
              <Badge variant="secondary" className="gap-1">
                <Loader2 className="size-3 animate-spin" />
                {activeCount} active
              </Badge>
            )}
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            Sites being opened and extracted in real time.
          </p>
        </div>
        <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
          <span>{rows.length} tracked</span>
          <span>·</span>
          <span>{extractedCount} extracted</span>
          {failedCount > 0 && (
            <>
              <span>·</span>
              <span>{failedCount} skipped/failed</span>
            </>
          )}
        </div>
      </div>

      <div className="max-h-[420px] space-y-2 overflow-auto pr-1">
        {rows.map((row) => (
          <SiteRow key={row.key} row={row} />
        ))}
      </div>
    </GlassCard>
  );
}

function SiteRow({ row }: { row: SiteActivityRow }) {
  const active = row.stage === "opening" || row.stage === "extracting";
  return (
    <div
      className={cn(
        "flex items-start gap-3 rounded-lg border border-border px-3 py-2.5 transition",
        active && "border-primary/40 bg-primary/5",
      )}
    >
      <StageDot stage={row.stage} />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <a
            href={row.url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex max-w-full items-center gap-1 truncate font-medium text-foreground hover:underline"
          >
            <span className="truncate">{row.title}</span>
            <ExternalLink className="size-3 shrink-0 opacity-60" />
          </a>
          <Badge
            variant="secondary"
            className={cn(
              "shrink-0",
              row.stage === "extracted" && "bg-emerald-500/15 text-emerald-700",
              row.stage === "failed" && "bg-destructive/15 text-destructive",
              row.stage === "blocked" && "bg-amber-500/15 text-amber-700",
              active && "bg-primary/15 text-primary",
            )}
          >
            {STAGE_LABEL[row.stage]}
          </Badge>
        </div>
        <p className="mt-0.5 truncate text-xs text-muted-foreground">
          {row.domain}
          {row.detail ? ` · ${row.detail}` : ""}
        </p>
      </div>
      {row.updatedAt && (
        <time className="shrink-0 text-[11px] text-muted-foreground">
          {new Date(row.updatedAt).toLocaleTimeString()}
        </time>
      )}
    </div>
  );
}

function StageDot({ stage }: { stage: SiteStage }) {
  const active = stage === "opening" || stage === "extracting";
  const color =
    stage === "extracted"
      ? "bg-emerald-500"
      : stage === "failed"
        ? "bg-destructive"
        : stage === "blocked"
          ? "bg-amber-500"
          : stage === "fetched"
            ? "bg-sky-500"
            : stage === "skipped" || stage === "unsupported"
              ? "bg-muted-foreground/50"
              : stage === "queued"
                ? "bg-muted-foreground/40"
                : active
                  ? "bg-primary"
                  : "bg-muted-foreground/30";
  return (
    <span className={cn("mt-1.5 size-2.5 shrink-0 rounded-full", color, active && "animate-pulse")} />
  );
}

export function buildSiteActivityRows(input: {
  candidates: SourceCandidate[];
  tasks: ScrapingTask[];
  attempts: SourceRetrievalAttempt[];
  documents: SourceDocument[];
  events: ScrapingEvent[];
}): SiteActivityRow[] {
  const { candidates, tasks, attempts, documents, events } = input;
  const candidatesById = new Map(candidates.map((c) => [c.id, c]));
  const documentsById = new Map(documents.map((d) => [d.id, d]));
  const docsByCandidate = new Map<string, SourceDocument>();
  for (const doc of documents) {
    docsByCandidate.set(doc.source_candidate_id, doc);
  }

  const latestAttemptByCandidate = new Map<string, SourceRetrievalAttempt>();
  for (const attempt of attempts) {
    const prev = latestAttemptByCandidate.get(attempt.source_candidate_id);
    if (!prev || new Date(attempt.started_at) > new Date(prev.started_at)) {
      latestAttemptByCandidate.set(attempt.source_candidate_id, attempt);
    }
  }

  const retrieveTasks = tasks.filter((task) => task.task_type === "retrieve_source");
  const taskByCandidate = new Map<string, ScrapingTask>();
  for (const task of retrieveTasks) {
    const candidateId = stringMeta(task.input_json, "source_candidate_id");
    if (!candidateId) continue;
    const prev = taskByCandidate.get(candidateId);
    if (!prev || new Date(task.updated_at) > new Date(prev.updated_at)) {
      taskByCandidate.set(candidateId, task);
    }
  }

  type Mutable = {
    stage: SiteStage;
    detail?: string;
    updatedAt?: string;
    title?: string;
    url?: string;
    domain?: string;
  };
  const state = new Map<string, Mutable>();

  function ensure(candidateId: string): Mutable {
    let row = state.get(candidateId);
    if (!row) {
      const candidate = candidatesById.get(candidateId);
      row = {
        stage: "discovered",
        title: candidate?.title || candidate?.canonical_url,
        url: candidate?.canonical_url || candidate?.url,
        domain: candidate?.domain,
        updatedAt: candidate?.discovered_at,
      };
      state.set(candidateId, row);
    }
    return row;
  }

  function bump(candidateId: string, stage: SiteStage, at?: string | null, detail?: string) {
    const row = ensure(candidateId);
    row.stage = stage;
    if (detail) row.detail = detail;
    if (at) row.updatedAt = at;
  }

  for (const candidate of candidates) {
    ensure(candidate.id);
  }

  for (const [candidateId, task] of taskByCandidate) {
    if (task.status === "queued" || task.status === "pending") {
      bump(candidateId, "queued", task.updated_at, "Waiting to open");
    } else if (task.status === "running") {
      bump(candidateId, "opening", task.started_at ?? task.updated_at, "Fetching page…");
    } else if (task.status === "failed") {
      bump(candidateId, "failed", task.completed_at ?? task.updated_at, task.error_message ?? "Retrieval failed");
    }
  }

  for (const [candidateId, attempt] of latestAttemptByCandidate) {
    const at = attempt.completed_at ?? attempt.started_at;
    if (attempt.status === "succeeded") {
      bump(candidateId, "fetched", at, "Page downloaded");
    } else if (attempt.status === "blocked_by_robots") {
      bump(candidateId, "blocked", at, "Blocked by robots.txt");
    } else if (attempt.status === "unsupported_content_type") {
      bump(candidateId, "unsupported", at, attempt.content_type ?? "Unsupported content");
    } else if (!["succeeded"].includes(attempt.status)) {
      // Don't overwrite an in-flight opening task with a stale failed attempt if task still running
      const task = taskByCandidate.get(candidateId);
      if (task?.status === "running") continue;
      bump(
        candidateId,
        "failed",
        at,
        attempt.safe_error_message ?? attempt.failure_classification ?? "Fetch failed",
      );
    }
  }

  for (const doc of documents) {
    const row = ensure(doc.source_candidate_id);
    if (STAGE_RANK[row.stage] > STAGE_RANK.fetched) {
      bump(doc.source_candidate_id, "fetched", doc.retrieval_timestamp, "Page saved");
    }
    if (!row.url) row.url = doc.final_url;
    if (!row.domain) row.domain = safeHostname(doc.final_url);
    if (!row.title) row.title = doc.final_url;
  }

  // Extraction events (document-scoped)
  for (const event of events) {
    const meta = event.metadata_json ?? {};
    const documentId = stringMeta(meta, "source_document_id");
    const candidateIdFromMeta = stringMeta(meta, "candidate_id") ?? stringMeta(meta, "source_candidate_id");
    const doc = documentId ? documentsById.get(documentId) : undefined;
    const candidateId = candidateIdFromMeta ?? doc?.source_candidate_id;
    if (!candidateId) continue;

    if (event.event_type === "source_retrieval_started") {
      const row = ensure(candidateId);
      const url = stringMeta(meta, "canonical_url");
      const title = stringMeta(meta, "title");
      const domain = stringMeta(meta, "domain");
      if (url) row.url = url;
      if (title) row.title = title;
      if (domain) row.domain = domain;
      const task = taskByCandidate.get(candidateId);
      if (!task || task.status === "running" || !latestAttemptByCandidate.has(candidateId)) {
        bump(candidateId, "opening", event.created_at, "Opening website…");
      }
      continue;
    }

    if (event.event_type === "facility_extraction_document_prepared") {
      bump(candidateId, "extracting", event.created_at, "Reading page for facilities…");
      continue;
    }

    if (event.event_type === "facility_extraction_document_completed") {
      const count = typeof meta.candidate_count === "number" ? meta.candidate_count : undefined;
      const hadFailure = meta.had_failure === true;
      bump(
        candidateId,
        hadFailure && (count ?? 0) === 0 ? "failed" : "extracted",
        event.created_at,
        count != null ? `${count} facilities found` : "Extraction finished",
      );
      continue;
    }

    if (event.event_type === "facility_extraction_document_skipped") {
      bump(
        candidateId,
        "skipped",
        event.created_at,
        stringMeta(meta, "failure_classification") ?? "Skipped before extraction",
      );
    }
  }

  // If extraction phase started and we have fetched docs without a terminal extract state, show extracting
  const extractionStarted = events.some((e) => e.event_type === "facility_extraction_phase_started");
  const extractionDone = events.some((e) => e.event_type === "facility_extraction_phase_completed");
  if (extractionStarted && !extractionDone) {
    for (const [candidateId, row] of state) {
      if (row.stage === "fetched" && docsByCandidate.has(candidateId)) {
        bump(candidateId, "extracting", row.updatedAt, "Waiting in extraction queue…");
      }
    }
  }

  const rows: SiteActivityRow[] = [];
  for (const [key, row] of state) {
    const url = row.url;
    if (!url) continue;
    // Prefer sites that entered the pipeline (not raw discovery backlog unless few items)
    rows.push({
      key,
      title: row.title?.trim() || url,
      url,
      domain: row.domain || safeHostname(url),
      stage: row.stage,
      detail: row.detail,
      updatedAt: row.updatedAt,
    });
  }

  // Prefer pipeline activity over pure discovery noise
  const pipeline = rows.filter((r) => r.stage !== "discovered");
  const display = pipeline.length > 0 ? pipeline : rows;

  display.sort((a, b) => {
    const rankDiff = STAGE_RANK[a.stage] - STAGE_RANK[b.stage];
    if (rankDiff !== 0) return rankDiff;
    const at = new Date(b.updatedAt ?? 0).getTime() - new Date(a.updatedAt ?? 0).getTime();
    return at;
  });

  return display.slice(0, 80);
}

function stringMeta(obj: Record<string, unknown>, key: string): string | null {
  const value = obj[key];
  return typeof value === "string" && value.length > 0 ? value : null;
}

function safeHostname(value?: string | null) {
  if (!value) return "";
  try {
    return new URL(value).hostname;
  } catch {
    return "";
  }
}
