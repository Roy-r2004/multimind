import { createFileRoute, Link, useNavigate, useParams } from "@tanstack/react-router";
import {
  ArrowLeft,
  ArrowRight,
  Building2,
  ChevronLeft,
  ChevronRight,
  Download,
  ExternalLink,
  Grid2x2,
  Loader2,
  Search,
  Sparkles,
  Trash2,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { DreamPageShell, DreamPanel } from "@/components/scraping/DreamPageShell";
import { CountryOutline } from "@/components/maps/CountryOutline";
import { MapsRunStatusBadge } from "@/components/maps/MapsRunStatusBadge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { countryFlagEmoji, getFlagColors } from "@/lib/maps/countryVisuals";
import {
  EXPORT_COLUMNS,
  placeToExportRow,
  sortPlacesForExport,
} from "@/lib/maps/exportDisplay";
import { cn } from "@/lib/utils";
import { useAuth } from "@/lib/auth";
import {
  advanceMapsCensusToPhase2,
  downloadMapsCensusExport,
  enrichMapsCensusRun,
  excludeMapsCensusPlace,
  getMapsCensusRun,
  getMapsCensusRunLiveStats,
  listMapsCensusCells,
  listMapsCensusPlaces,
  refreshMapsCensusWebsites,
} from "@/lib/maps/api";
import type {
  MapsCensusCellItem,
  MapsCensusRunDetail,
  MapsPaginatedMeta,
  MapsPlaceItem,
  MapsRunLiveStats,
} from "@/lib/maps/types";

const ACTIVE_STATUSES = new Set(["queued", "running"]);
/** Terminal success — includes campaigns finished with optional-stage warnings. */
const SUCCESS_STATUSES = new Set(["completed", "completed_with_warnings"]);
const POLL_INTERVAL_MS = 5000;
const ENRICHMENT_BUSY = new Set(["pending", "running"]);
const PHASE_PAGE_SIZE = 50;
const EMPTY_META: MapsPaginatedMeta = { total: 0, limit: PHASE_PAGE_SIZE, offset: 0 };

/** A Phase 2 row with no phone and no website is undeliverable to the client
 * — matches the same requirement the Phase 2 Excel export enforces server-side. */
function hasPhoneAndWebsite(place: MapsPlaceItem): boolean {
  const hasPhone = Boolean((place.international_phone_number || "").trim());
  const hasWebsite = Boolean((place.official_website || place.raw_website || "").trim());
  return hasPhone && hasWebsite;
}

function enrichmentStillRunning(
  run: {
    status: string;
    completed_at?: string | null;
    enrichment_refresh_completed_at?: string | null;
  },
  places: { enrichment_status: string }[],
  enrichmentPollUntil: number | null,
): boolean {
  if (!SUCCESS_STATUSES.has(run.status)) return false;
  // Enrichment job finished — stop even if some rows stayed empty.
  if (run.enrichment_refresh_completed_at) return false;
  if (!places.some((place) => ENRICHMENT_BUSY.has(place.enrichment_status))) return false;
  if (enrichmentPollUntil != null && Date.now() < enrichmentPollUntil) return true;
  // Don't poll forever on old runs that never started enrichment.
  if (!run.completed_at) return false;
  const ageMs = Date.now() - new Date(run.completed_at).getTime();
  return Number.isFinite(ageMs) && ageMs >= 0 && ageMs < 15 * 60 * 1000;
}

export const Route = createFileRoute("/maps/$runId")({
  head: () => ({ meta: [{ title: "Maps Census Run - MultiAI" }] }),
  component: MapsRunDetailPage,
});

function MapsRunDetailPage() {
  const { runId } = useParams({ from: "/maps/$runId" });
  const { authHeaders } = useAuth();
  const navigate = useNavigate();
  const [run, setRun] = useState<MapsCensusRunDetail | null>(null);
  const [searchCells, setSearchCells] = useState<MapsCensusCellItem[]>([]);
  const [liveStats, setLiveStats] = useState<MapsRunLiveStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [enriching, setEnriching] = useState(false);
  const [pollTick, setPollTick] = useState(0);
  const [enrichmentPollUntil, setEnrichmentPollUntil] = useState<number | null>(null);

  const [activeTab, setActiveTab] = useState<"phase1" | "phase2">("phase1");
  const [phase1Places, setPhase1Places] = useState<MapsPlaceItem[]>([]);
  const [phase1Meta, setPhase1Meta] = useState<MapsPaginatedMeta>(EMPTY_META);
  const [phase1Offset, setPhase1Offset] = useState(0);
  const [phase2Places, setPhase2Places] = useState<MapsPlaceItem[]>([]);
  const [phase2Meta, setPhase2Meta] = useState<MapsPaginatedMeta>(EMPTY_META);
  const [phase2Offset, setPhase2Offset] = useState(0);
  const [phase2Triggered, setPhase2Triggered] = useState(false);
  const [advancing, setAdvancing] = useState(false);
  const [removingPlaceId, setRemovingPlaceId] = useState<string | null>(null);
  const [exportingPhase, setExportingPhase] = useState<"phase1" | "phase2" | null>(null);

  useEffect(() => {
    const auth = authHeaders();
    if (!auth) {
      void navigate({ to: "/login" });
      return;
    }
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function load() {
      try {
        const [runDetail, cellItems, phase1Resp, phase2Resp] = await Promise.all([
          getMapsCensusRun(auth!, runId),
          listMapsCensusCells(auth!, runId),
          listMapsCensusPlaces(auth!, runId, { limit: PHASE_PAGE_SIZE, offset: phase1Offset }),
          listMapsCensusPlaces(auth!, runId, {
            keepDropDecision: "keep",
            limit: PHASE_PAGE_SIZE,
            offset: phase2Offset,
          }),
        ]);
        if (cancelled) return;
        setRun(runDetail);
        setSearchCells(cellItems);
        setPhase1Places(phase1Resp.items);
        setPhase1Meta(phase1Resp.meta);
        setPhase2Places(phase2Resp.items);
        setPhase2Meta(phase2Resp.meta);
        if (phase2Resp.meta.total > 0) setPhase2Triggered(true);
        setError(null);

        // Fetch live stats if run is active (for real-time stat tile updates)
        if (ACTIVE_STATUSES.has(runDetail.status)) {
          try {
            const stats = await getMapsCensusRunLiveStats(auth!, runId);
            if (!cancelled) setLiveStats(stats);
          } catch {
            // Live stats fetch failed, but don't block the main load
          }
        }

        if (
          ACTIVE_STATUSES.has(runDetail.status) ||
          enrichmentStillRunning(runDetail, phase2Resp.items, enrichmentPollUntil)
        ) {
          timer = setTimeout(load, POLL_INTERVAL_MS);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load Maps census run");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    setLoading(true);
    void load();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [authHeaders, navigate, runId, pollTick, enrichmentPollUntil, phase1Offset, phase2Offset]);

  async function handleFindMissingWebsites() {
    const auth = authHeaders();
    if (!auth) return;
    setRefreshing(true);
    setError(null);
    try {
      const updated = await refreshMapsCensusWebsites(auth, runId);
      setRun(updated);
      setPollTick((tick) => tick + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start website search");
    } finally {
      setRefreshing(false);
    }
  }

  async function handleDownloadExport(scope: "phase1" | "phase2") {
    const auth = authHeaders();
    if (!auth || !run) return;
    setExportingPhase(scope);
    setError(null);
    try {
      await downloadMapsCensusExport(auth, runId, scope);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to download Excel export");
    } finally {
      setExportingPhase(null);
    }
  }

  async function handleRemovePlace(placeId: string) {
    const auth = authHeaders();
    if (!auth) return;
    setRemovingPlaceId(placeId);
    setError(null);
    try {
      await excludeMapsCensusPlace(auth, runId, placeId);
      setPhase1Places((prev) => prev.filter((place) => place.id !== placeId));
      setPhase2Places((prev) => prev.filter((place) => place.id !== placeId));
      setPollTick((tick) => tick + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to remove facility");
    } finally {
      setRemovingPlaceId(null);
    }
  }

  async function handleAdvanceToPhase2() {
    const auth = authHeaders();
    if (!auth) return;
    setAdvancing(true);
    setError(null);
    setEnrichmentPollUntil(Date.now() + 15 * 60 * 1000);
    try {
      await advanceMapsCensusToPhase2(auth, runId);
      setPhase2Triggered(true);
      setActiveTab("phase2");
      setPollTick((tick) => tick + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start Phase 2");
    } finally {
      setAdvancing(false);
    }
  }

  async function handleEnrichWebsites() {
    const auth = authHeaders();
    if (!auth) return;
    setEnriching(true);
    setError(null);
    setEnrichmentPollUntil(Date.now() + 15 * 60 * 1000);
    try {
      const updated = await enrichMapsCensusRun(auth, runId);
      setRun(updated);
      setPollTick((tick) => tick + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start AI enrichment");
    } finally {
      setEnriching(false);
    }
  }

  const phase1Rows = useMemo(() => {
    if (!run) return [];
    return sortPlacesForExport(phase1Places).map((place) => placeToExportRow(place, run.country_name));
  }, [phase1Places, run]);

  const phase2ContactablePlaces = useMemo(
    () => phase2Places.filter((place) => hasPhoneAndWebsite(place)),
    [phase2Places],
  );
  const phase2Rows = useMemo(() => {
    if (!run) return [];
    return sortPlacesForExport(phase2ContactablePlaces).map((place) =>
      placeToExportRow(place, run.country_name),
    );
  }, [phase2ContactablePlaces, run]);

  return (
    <AppShell>
      <DreamPageShell maxWidth="max-w-[96rem]">
        {!run && (
          <Link
            to="/maps"
            className="mb-8 inline-flex w-fit items-center gap-2 text-sm text-muted-foreground transition hover:text-primary"
          >
            <ArrowLeft className="size-3.5" />
            All Maps census runs
          </Link>
        )}

        {loading && !run && (
          <DreamPanel className="text-sm text-muted-foreground">Loading run…</DreamPanel>
        )}
        {error && <DreamPanel className="mb-6 text-sm text-rose-600">{error}</DreamPanel>}

        {run && (
          <>
            <MapsRunHero
              run={run}
              refreshing={refreshing}
              enriching={enriching}
              onFindMissingWebsites={() => void handleFindMissingWebsites()}
              onEnrichWebsites={() => void handleEnrichWebsites()}
            />
            {run.error_message && (
              <DreamPanel className="mt-6 text-sm text-rose-600">{run.error_message}</DreamPanel>
            )}

            <div className="mt-8 grid grid-cols-2 gap-4 sm:grid-cols-4">
              <MapsStatCard
                icon={<Building2 className="size-4" />}
                label="Relevant facilities"
                value={
                  liveStats && ACTIVE_STATUSES.has(run.status)
                    ? liveStats.places_relevant_live
                    : run.places_classified_relevant
                }
                tone="teal"
                emphasize
                isLive={liveStats !== null && ACTIVE_STATUSES.has(run.status)}
              />
              <MapsStatCard
                icon={<Search className="size-4" />}
                label="Places scanned"
                value={
                  liveStats && ACTIVE_STATUSES.has(run.status)
                    ? liveStats.places_found_live
                    : run.places_found
                }
                tone="amber"
                isLive={liveStats !== null && ACTIVE_STATUSES.has(run.status)}
              />
              <MapsStatCard
                icon={<Grid2x2 className="size-4" />}
                label="Search queries"
                value={searchCells.length}
                tone="violet"
              />
            </div>

            <Tabs
              value={activeTab}
              onValueChange={(value) => setActiveTab(value as "phase1" | "phase2")}
              className="mt-8"
            >
              <TabsList>
                <TabsTrigger value="phase1">Phase 1 · Discovery ({phase1Meta.total})</TabsTrigger>
                <TabsTrigger value="phase2">Phase 2 · Eligible ({phase2Meta.total})</TabsTrigger>
              </TabsList>

              <TabsContent value="phase1" className="mt-4">
                <FacilitiesExportTable
                  rows={phase1Rows}
                  isRunning={ACTIVE_STATUSES.has(run.status)}
                  emptyMessage={
                    ACTIVE_STATUSES.has(run.status)
                      ? "Census still running — facility rows will appear here as they are found."
                      : "No facilities were discovered for this country."
                  }
                  title="Phase 1 · Discovery results"
                  description="Every facility found via the Google Maps keyword search. Remove anything that doesn't belong, then proceed to Phase 2 when ready."
                  onRemove={(placeId) => void handleRemovePlace(placeId)}
                  removingPlaceId={removingPlaceId}
                  headerExtra={
                    <div className="flex flex-wrap items-center gap-2">
                      <button
                        type="button"
                        onClick={() => void handleDownloadExport("phase1")}
                        disabled={exportingPhase === "phase1" || phase1Meta.total === 0}
                        className="inline-flex items-center gap-2 rounded-xl border border-border px-3 py-1.5 text-xs font-semibold text-foreground transition hover:bg-muted/50 disabled:opacity-50"
                      >
                        {exportingPhase === "phase1" ? (
                          <Loader2 className="size-3.5 animate-spin" />
                        ) : (
                          <Download className="size-3.5" />
                        )}
                        Export Excel
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleAdvanceToPhase2()}
                        disabled={advancing || phase1Meta.total === 0 || ACTIVE_STATUSES.has(run.status)}
                        title={
                          ACTIVE_STATUSES.has(run.status)
                            ? "Wait for Phase 1 discovery to finish before proceeding"
                            : undefined
                        }
                        className="inline-flex items-center gap-2 rounded-xl bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground shadow-sm transition hover:bg-primary/90 disabled:opacity-50"
                      >
                        {advancing ? (
                          <Loader2 className="size-3.5 animate-spin" />
                        ) : (
                          <ArrowRight className="size-3.5" />
                        )}
                        Proceed to Phase 2
                      </button>
                    </div>
                  }
                />
                {ACTIVE_STATUSES.has(run.status) && (
                  <p className="mt-2 text-xs text-muted-foreground">
                    Phase 1 discovery is still running — "Proceed to Phase 2" will be enabled once it
                    completes.
                  </p>
                )}
                <PaginationControls
                  meta={phase1Meta}
                  onPrev={() => setPhase1Offset((offset) => Math.max(0, offset - PHASE_PAGE_SIZE))}
                  onNext={() => setPhase1Offset((offset) => offset + PHASE_PAGE_SIZE)}
                />
              </TabsContent>

              <TabsContent value="phase2" className="mt-4">
                {!phase2Triggered && phase2Meta.total === 0 ? (
                  <DreamPanel className="p-6 text-sm text-muted-foreground">
                    Phase 2 hasn't run yet. Review Phase 1's discovery results, remove anything
                    that doesn't belong, then click "Proceed to Phase 2" to run the strict
                    eligibility gate.
                  </DreamPanel>
                ) : (
                  <>
                    <FacilitiesExportTable
                      rows={phase2Rows}
                      isRunning={advancing}
                      emptyMessage={
                        advancing
                          ? "Running the eligibility gate — this can take a few minutes."
                          : "No facilities passed the eligibility gate for this country."
                      }
                      title="Phase 2 · Eligible facilities"
                      description="The final client-ready list, after the strict keep/drop gate and AI enrichment."
                      headerExtra={
                        <button
                          type="button"
                          onClick={() => void handleDownloadExport("phase2")}
                          disabled={exportingPhase === "phase2" || phase2Meta.total === 0}
                          className="inline-flex items-center gap-2 rounded-xl border border-border px-3 py-1.5 text-xs font-semibold text-foreground transition hover:bg-muted/50 disabled:opacity-50"
                        >
                          {exportingPhase === "phase2" ? (
                            <Loader2 className="size-3.5 animate-spin" />
                          ) : (
                            <Download className="size-3.5" />
                          )}
                          Export Excel
                        </button>
                      }
                    />
                    <PaginationControls
                      meta={phase2Meta}
                      onPrev={() => setPhase2Offset((offset) => Math.max(0, offset - PHASE_PAGE_SIZE))}
                      onNext={() => setPhase2Offset((offset) => offset + PHASE_PAGE_SIZE)}
                    />
                  </>
                )}
              </TabsContent>
            </Tabs>
          </>
        )}
      </DreamPageShell>
    </AppShell>
  );
}

function PaginationControls({
  meta,
  onPrev,
  onNext,
}: {
  meta: MapsPaginatedMeta;
  onPrev: () => void;
  onNext: () => void;
}) {
  if (meta.total <= meta.limit) return null;
  const from = meta.total === 0 ? 0 : meta.offset + 1;
  const to = Math.min(meta.total, meta.offset + meta.limit);
  return (
    <div className="mt-3 flex items-center justify-between text-sm text-muted-foreground">
      <span>
        Showing {from}–{to} of {meta.total}
      </span>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={onPrev}
          disabled={meta.offset === 0}
          className="inline-flex items-center gap-1 rounded-lg border border-border px-2.5 py-1.5 text-xs font-medium transition hover:bg-muted/50 disabled:opacity-40"
        >
          <ChevronLeft className="size-3.5" />
          Prev
        </button>
        <button
          type="button"
          onClick={onNext}
          disabled={meta.offset + meta.limit >= meta.total}
          className="inline-flex items-center gap-1 rounded-lg border border-border px-2.5 py-1.5 text-xs font-medium transition hover:bg-muted/50 disabled:opacity-40"
        >
          Next
          <ChevronRight className="size-3.5" />
        </button>
      </div>
    </div>
  );
}

function FacilitiesExportTable({
  rows,
  isRunning,
  emptyMessage,
  title,
  description,
  headerExtra,
  onRemove,
  removingPlaceId,
}: {
  rows: ReturnType<typeof placeToExportRow>[];
  isRunning: boolean;
  emptyMessage: string;
  title: string;
  description: string;
  headerExtra?: React.ReactNode;
  onRemove?: (placeId: string) => void;
  removingPlaceId?: string | null;
}) {
  const hasRows = rows.length > 0;
  const columnCount = EXPORT_COLUMNS.length + (onRemove ? 1 : 0);

  return (
    <DreamPanel className="p-0 overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border/80 px-5 py-4">
        <div>
          <h2 className="font-display text-base font-semibold text-foreground">{title}</h2>
          <p className="mt-0.5 text-sm text-muted-foreground">{description}</p>
        </div>
        {headerExtra}
      </div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[1100px] border-collapse text-sm">
          <thead className="sticky top-0 z-10 bg-emerald-50/95 text-left text-[11px] uppercase tracking-[0.06em] text-emerald-900 dark:bg-emerald-950/90 dark:text-emerald-100">
            <tr className="border-b border-emerald-200/80 dark:border-emerald-900">
              {EXPORT_COLUMNS.map((column) => (
                <th key={column} className="px-3 py-2.5 font-semibold whitespace-nowrap">
                  {column}
                </th>
              ))}
              {onRemove && <th className="px-3 py-2.5 font-semibold whitespace-nowrap">Remove</th>}
            </tr>
          </thead>
          <tbody>
            {!hasRows ? (
              <tr>
                <td colSpan={columnCount} className="px-5 py-8 text-sm text-muted-foreground">
                  {isRunning ? (
                    <span className="inline-flex items-center gap-2">
                      <Loader2 className="size-3.5 animate-spin" />
                      {emptyMessage}
                    </span>
                  ) : (
                    emptyMessage
                  )}
                </td>
              </tr>
            ) : (
              rows.map(({ place, cells }) => (
                <tr
                  key={place.id}
                  className={cn(
                    "border-b border-border/60 align-top",
                    "odd:bg-background even:bg-muted/15",
                    !place.export_eligible && "opacity-75",
                  )}
                >
                  {EXPORT_COLUMNS.map((column) => (
                    <ExportTableCell
                      key={`${place.id}-${column}`}
                      column={column}
                      value={cells[column]}
                    />
                  ))}
                  {onRemove && (
                    <td className="px-3 py-2.5 align-top">
                      <button
                        type="button"
                        onClick={() => onRemove(place.id)}
                        disabled={removingPlaceId === place.id}
                        aria-label={`Remove ${place.canonical_name}`}
                        className="inline-flex items-center gap-1 rounded-lg border border-border px-2 py-1 text-xs font-medium text-muted-foreground transition hover:border-rose-300 hover:text-rose-600 disabled:opacity-50"
                      >
                        {removingPlaceId === place.id ? (
                          <Loader2 className="size-3.5 animate-spin" />
                        ) : (
                          <Trash2 className="size-3.5" />
                        )}
                      </button>
                    </td>
                  )}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </DreamPanel>
  );
}

function ExportTableCell({
  column,
  value,
}: {
  column: string;
  value: string;
}) {
  const isPlaceholder = value === "Not Specified" || value === "Contact for pricing";
  const isWebsiteLink = column === "Website" && !isPlaceholder;
  const isEmailLink = column === "Email" && !isPlaceholder;

  return (
    <td className="min-w-[8rem] max-w-[18rem] px-3 py-2.5 align-top text-foreground">
      {isWebsiteLink ? (
        <a
          href={value}
          target="_blank"
          rel="noreferrer"
          className="inline-flex max-w-full items-start gap-1 text-primary hover:underline"
        >
          <span className="break-all">{value}</span>
          <ExternalLink className="mt-0.5 size-3 shrink-0" />
        </a>
      ) : isEmailLink ? (
        <a href={`mailto:${value}`} className="break-all text-primary hover:underline">
          {value}
        </a>
      ) : (
        <span
          className={cn(
            "block whitespace-pre-wrap break-words",
            isPlaceholder && "italic text-muted-foreground",
          )}
        >
          {value}
        </span>
      )}
    </td>
  );
}

function MapsRunHero({
  run,
  refreshing,
  enriching,
  onFindMissingWebsites,
  onEnrichWebsites,
}: {
  run: MapsCensusRunDetail;
  refreshing: boolean;
  enriching: boolean;
  onFindMissingWebsites: () => void;
  onEnrichWebsites: () => void;
}) {
  const [primary, secondary] = getFlagColors(run.country_code);
  const isSuccess = SUCCESS_STATUSES.has(run.status);
  const showFindMissingWebsites =
    isSuccess && run.places_classified_relevant > run.places_with_website;
  const showEnrichWebsites =
    isSuccess &&
    run.places_classified_relevant > 0 &&
    run.places_enriched < run.places_classified_relevant;

  return (
    <div className="dream-rise relative isolate overflow-hidden rounded-[1.75rem] border border-white/10 bg-slate-950 shadow-[0_16px_44px_oklch(0.2_0.06_240/0.35)]">
      {run.hero_image_url ? (
        <>
          <img
            src={run.hero_image_url}
            alt=""
            aria-hidden="true"
            className="absolute inset-0 size-full object-cover"
          />
          <div className="absolute inset-0 bg-gradient-to-t from-slate-950/92 via-slate-950/65 to-slate-950/25" />
        </>
      ) : (
        <>
          <div className="absolute inset-0 bg-gradient-to-br from-slate-950 via-slate-900 to-slate-950" />
          <div
            aria-hidden="true"
            className="absolute inset-0 opacity-90"
            style={{
              backgroundImage: `radial-gradient(120% 120% at 90% 8%, ${primary}66 0%, transparent 44%), radial-gradient(120% 120% at 4% 118%, ${secondary}55 0%, transparent 48%)`,
            }}
          />
        </>
      )}
      <CountryOutline
        countryCode={run.country_code}
        className="absolute -right-6 -top-10 h-[22rem] w-[22rem] opacity-90 sm:h-[26rem] sm:w-[26rem]"
      />

      <div className="relative flex flex-col gap-6 p-6 sm:p-8">
        <Link
          to="/maps"
          className="inline-flex w-fit items-center gap-2 text-sm text-white/70 transition hover:text-white"
        >
          <ArrowLeft className="size-3.5" />
          All Maps census runs
        </Link>

        <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.32em] text-white/70">
              Maps Census
            </p>
            <div className="mt-1 flex flex-wrap items-center gap-2">
              <span aria-hidden="true" className="text-2xl leading-none">
                {countryFlagEmoji(run.country_code)}
              </span>
              <h1 className="font-display text-3xl font-semibold tracking-tight text-white sm:text-4xl">
                {run.country_name}
              </h1>
            </div>
            <p className="mt-2 max-w-2xl text-sm leading-relaxed text-white/75">
              Non-government inpatient addiction rehab census — private/NGO centers and
              private addictologues. Search keywords and export columns in spreadsheet view.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            {showEnrichWebsites && (
              <button
                type="button"
                onClick={onEnrichWebsites}
                disabled={enriching}
                className="inline-flex items-center gap-2 rounded-xl bg-white/90 px-3.5 py-2 text-xs font-semibold text-slate-900 shadow-sm transition hover:bg-white disabled:opacity-50"
              >
                {enriching ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <Sparkles className="size-3.5" />
                )}
                Enrich with AI
              </button>
            )}
            {showFindMissingWebsites && (
              <button
                type="button"
                onClick={onFindMissingWebsites}
                disabled={refreshing}
                className="inline-flex items-center gap-2 rounded-xl bg-white px-3.5 py-2 text-xs font-semibold text-slate-900 shadow-sm transition hover:bg-white/90 disabled:opacity-50"
              >
                {refreshing ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <Sparkles className="size-3.5" />
                )}
                Find missing websites
              </button>
            )}
            <MapsRunStatusBadge status={run.status} />
          </div>
        </div>
      </div>
    </div>
  );
}

function MapsStatCard({
  icon,
  label,
  value,
  tone,
  emphasize,
  isLive,
}: {
  icon: React.ReactNode;
  label: string;
  value: string | number;
  tone: "teal" | "sky" | "amber" | "violet";
  emphasize?: boolean;
  isLive?: boolean;
}) {
  const tones: Record<typeof tone, string> = {
    teal: "bg-teal-100 text-teal-700",
    sky: "bg-sky-100 text-sky-700",
    amber: "bg-amber-100 text-amber-800",
    violet: "bg-indigo-100 text-indigo-700",
  };
  return (
    <div
      className={cn(
        "dream-rise relative overflow-hidden rounded-2xl border p-4 backdrop-blur-[2px]",
        emphasize
          ? "border-primary/25 bg-gradient-to-br from-teal-50/90 via-card to-sky-50 shadow-[0_12px_32px_oklch(0.55_0.1_240/0.1)]"
          : "border-border/90 bg-card/95 shadow-[0_8px_24px_oklch(0.45_0.04_240/0.06)]",
        isLive && "animate-pulse",
      )}
    >
      <div className="flex items-start justify-between">
        <span className={cn("grid size-9 place-items-center rounded-full", tones[tone])}>
          {icon}
        </span>
        {isLive && (
          <span className="text-[10px] font-semibold text-green-600 uppercase tracking-wider">
            LIVE
          </span>
        )}
      </div>
      <div className="mt-3 font-display text-2xl font-semibold tracking-tight text-foreground">
        {value}
      </div>
      <div className="mt-0.5 text-[11px] uppercase tracking-[0.1em] text-muted-foreground">
        {label}
      </div>
    </div>
  );
}
