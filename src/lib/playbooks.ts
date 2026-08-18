/** My Playbooks Phase 5: page-state, polling, and display helpers. */

import type {
  ApiPlaybook,
  ApiPlaybookObservation,
  ApiPlaybookObservationFilters,
  ApiPlaybookRun,
} from "./api/types";

export const PLAYBOOK_POLL_INTERVAL_MS = 2000;
export const PLAYBOOK_MINE_PATH = "/playbooks/me";
export const PLAYBOOK_GENERATE_PATH = "/playbooks/me/generate";
export const PLAYBOOK_PENDING_PATH = "/playbooks/me/pending";
export const PLAYBOOK_RERUN_PATH = "/playbooks/me/rerun";
export const PLAYBOOK_LATEST_RUN_PATH = "/playbooks/me/runs/latest";

export function playbookRunPath(runId: string): string {
  return `/playbooks/me/runs/${encodeURIComponent(runId)}`;
}

export const PLAYBOOK_PAGE_TITLE = "My Playbooks";
export const PLAYBOOK_PAGE_SUBTITLE =
  "A structured understanding of how you work, what you are building, and the decisions you have made across MultiMind.";
export const PLAYBOOK_SAVED_COPY = "Your Playbook has been generated and saved.";
export const PLAYBOOK_RERUN_NOTE =
  "Reruns process only sources changed since the last successful version.";
export const PLAYBOOK_EMPTY_SUMMARY =
  "Your Playbook was generated, but no summary is currently available.";
export const PLAYBOOK_FINALIZING_COPY = "Finalizing observations and summary…";

export const PLAYBOOK_IN_FLIGHT_STATUSES = new Set(["queued", "processing"]);
export const PLAYBOOK_TERMINAL_STATUSES = new Set([
  "completed",
  "completed_with_warnings",
  "failed",
]);
export const PLAYBOOK_SUCCESS_STATUSES = new Set(["completed", "completed_with_warnings"]);
export const PLAYBOOK_FATAL_POLL_STATUSES = new Set([401, 403, 404]);

export const OBSERVATION_CATEGORY_ORDER = [
  "preference",
  "project",
  "architecture",
  "decision",
  "completed_work",
  "priority",
  "blocker",
  "next_step",
  "plan",
  "important_fact",
  "relationship",
  "rejected_option",
  "superseded_information",
] as const;

const KNOWN_OBSERVATION_CATEGORIES = new Set<string>(OBSERVATION_CATEGORY_ORDER);

const OBSERVATION_CATEGORY_LABELS: Record<string, string> = {
  preference: "Preferences",
  project: "Projects",
  architecture: "Architecture",
  decision: "Decisions",
  completed_work: "Completed Work",
  blocker: "Blockers",
  priority: "Priorities",
  next_step: "Next Steps",
  plan: "Plan",
  rejected_option: "Rejected Options",
  superseded_information: "Superseded Information",
  important_fact: "Important Facts",
  relationship: "Relationships",
};

const OBSERVATION_STATUS_LABELS: Record<string, string> = {
  active: "Active",
  confirmed: "Confirmed",
  planned: "Planned",
  completed: "Completed",
  rejected: "Rejected",
  superseded: "Superseded",
  uncertain: "Uncertain",
};

export type PlaybookPageState =
  | "initial_loading"
  | "load_error"
  | "not_generated"
  | "queued"
  | "processing"
  | "failed_without_playbook"
  | "active"
  | "active_with_warnings";

export type PlaybookPrimaryAction = "generate" | "retry" | "none";

export type PlaybookObservationGroup = {
  category: string;
  label: string;
  items: ApiPlaybookObservation[];
};

export function isPlaybookInFlight(status: string | null | undefined): boolean {
  return Boolean(status && PLAYBOOK_IN_FLIGHT_STATUSES.has(status));
}

export function isPlaybookTerminal(status: string | null | undefined): boolean {
  return Boolean(status && PLAYBOOK_TERMINAL_STATUSES.has(status));
}

export function isPlaybookSuccess(status: string | null | undefined): boolean {
  return Boolean(status && PLAYBOOK_SUCCESS_STATUSES.has(status));
}

export function playbookHasGeneratedVersion(playbook: ApiPlaybook | null): boolean {
  if (!playbook) return false;
  return (
    playbook.status === "active" ||
    playbook.playbook_version > 0 ||
    Boolean(playbook.last_success_run_id)
  );
}

export function shouldFetchPlaybookObservations(playbook: ApiPlaybook | null): boolean {
  return playbookHasGeneratedVersion(playbook);
}

export function derivePlaybookPageState(input: {
  loading: boolean;
  loadError: string | null;
  playbook: ApiPlaybook | null;
  run: ApiPlaybookRun | null;
}): PlaybookPageState {
  if (input.loading && !input.playbook) return "initial_loading";
  if (input.loadError && !input.playbook) return "load_error";
  if (playbookHasGeneratedVersion(input.playbook)) {
    if (
      input.run?.status === "completed_with_warnings" ||
      (isPlaybookSuccess(input.run?.status) && (input.run?.warning_count ?? 0) > 0)
    ) {
      return "active_with_warnings";
    }
    return "active";
  }
  if (input.run?.status === "queued") return "queued";
  if (input.run?.status === "processing") return "processing";
  if (input.run?.status === "failed") return "failed_without_playbook";
  return "not_generated";
}

export function playbookPrimaryAction(state: PlaybookPageState): PlaybookPrimaryAction {
  if (state === "not_generated") return "generate";
  if (state === "failed_without_playbook") return "retry";
  return "none";
}

export function playbookShowsGenerateButton(state: PlaybookPageState): boolean {
  return playbookPrimaryAction(state) === "generate";
}

export function playbookShowsRetryButton(state: PlaybookPageState): boolean {
  return playbookPrimaryAction(state) === "retry";
}

export function playbookShowsRerunButton(pendingSourceItems = 0): boolean {
  return pendingSourceItems > 0;
}

export function playbookProgressPercent(processedCount: number, totalCount: number): number {
  if (totalCount <= 0) return 0;
  const raw = (processedCount / totalCount) * 100;
  if (!Number.isFinite(raw)) return 0;
  return Math.min(100, Math.max(0, raw));
}

export function isPlaybookFinalizing(run: ApiPlaybookRun | null): boolean {
  if (!run || run.status !== "processing") return false;
  return run.total_count > 0 && run.processed_count >= run.total_count;
}

export function shouldResumePlaybookPolling(run: ApiPlaybookRun | null): boolean {
  return isPlaybookInFlight(run?.status);
}

export function shouldApplyPolledRun(
  expectedRunId: string | null,
  received: ApiPlaybookRun,
): boolean {
  return Boolean(expectedRunId && received.id === expectedRunId);
}

export function shouldRefetchPlaybookAfterRun(run: ApiPlaybookRun): boolean {
  return isPlaybookSuccess(run.status);
}

export function shouldStopPollingForStatus(httpStatus: number): boolean {
  return PLAYBOOK_FATAL_POLL_STATUSES.has(httpStatus);
}

export function buildPlaybookObservationsPath(filters?: ApiPlaybookObservationFilters): string {
  const search = new URLSearchParams();
  if (filters?.category) search.set("category", filters.category);
  if (filters?.status) search.set("status", filters.status);
  if (filters?.include_excluded === true) search.set("include_excluded", "true");
  const qs = search.toString();
  return `/playbooks/me/observations${qs ? `?${qs}` : ""}`;
}

export function observationCategoryLabel(category: string): string {
  return OBSERVATION_CATEGORY_LABELS[category] ?? titleizeToken(category);
}

export function observationStatusLabel(status: string): string {
  return OBSERVATION_STATUS_LABELS[status] ?? titleizeToken(status);
}

export function observationConfidenceLabel(
  confidence: number | null | undefined,
): "High confidence" | "Medium confidence" | "Low confidence" | null {
  if (confidence == null || !Number.isFinite(confidence)) return null;
  if (confidence >= 0.8) return "High confidence";
  if (confidence >= 0.5) return "Medium confidence";
  return "Low confidence";
}

export function groupObservationsByCategory(
  observations: ApiPlaybookObservation[],
): PlaybookObservationGroup[] {
  const buckets = new Map<string, ApiPlaybookObservation[]>();
  for (const item of observations) {
    const list = buckets.get(item.category) ?? [];
    list.push(item);
    buckets.set(item.category, list);
  }
  const known = OBSERVATION_CATEGORY_ORDER.filter((category) => buckets.has(category)).map(
    (category) => ({
      category,
      label: observationCategoryLabel(category),
      items: buckets.get(category) ?? [],
    }),
  );
  const unknown = [...buckets.keys()]
    .filter((category) => !KNOWN_OBSERVATION_CATEGORIES.has(category))
    .map((category) => ({
      category,
      label: observationCategoryLabel(category),
      items: buckets.get(category) ?? [],
    }));
  return [...known, ...unknown];
}

export function playbookWarningCopy(run: ApiPlaybookRun | null): string | null {
  const count = run?.warning_count ?? 0;
  if (count <= 0) return null;
  const noun = count === 1 ? "warning was" : "warnings were";
  return `${count} source-processing ${noun} recorded`;
}

export function playbookRunStatusLabel(status: string): string {
  switch (status) {
    case "queued":
      return "Queued";
    case "processing":
      return "Processing";
    case "completed":
    case "completed_with_warnings":
      return "Completed";
    case "failed":
      return "Failed";
    default:
      return titleizeToken(status);
  }
}

export function formatPlaybookTimestamp(iso: string | null | undefined): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function playbookCoreSummaryText(summary: string | null | undefined): string {
  const trimmed = (summary ?? "").trim();
  return trimmed || PLAYBOOK_EMPTY_SUMMARY;
}

export function copyClaimsPromptInjection(text: string): boolean {
  const lower = text.toLowerCase();
  return (
    lower.includes("active in every") ||
    lower.includes("council is now using") ||
    lower.includes("referee is using") ||
    lower.includes("every question receives") ||
    lower.includes("personalization is active in chat") ||
    lower.includes("active in every chat")
  );
}

function titleizeToken(value: string): string {
  return value
    .split(/[_-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}
