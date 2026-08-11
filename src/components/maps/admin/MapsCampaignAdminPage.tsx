import { Link } from "@tanstack/react-router";
import {
  AlertTriangle,
  ArrowLeft,
  ChevronDown,
  ChevronRight,
  Download,
  ExternalLink,
  Loader2,
  Pause,
  Play,
  RefreshCw,
  RotateCcw,
  Search,
  Square,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AdminError,
  AdminLoading,
  DataTable,
  formatDt,
  StatCard,
} from "@/components/admin/AdminUi";
import { MapsRunStatusBadge } from "@/components/maps/MapsRunStatusBadge";
import { GlassCard } from "@/components/cinematic/PageChrome";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useAuth } from "@/lib/auth";
import { downloadMapsCensusExport } from "@/lib/maps/api";
import {
  applyMapsPlaceReview,
  cancelMapsCensusRun,
  getMapsCensusAdminDashboard,
  getMapsCensusAdminPlaceDetail,
  getMapsCensusExportSummary,
  listMapsCensusAdminCells,
  listMapsCensusAdminPlaces,
  listMapsCensusAdminRegions,
  pauseMapsCensusRun,
  resumeMapsCensusRun,
  retryMapsCensusEnrichment,
  retryMapsCensusFailedCells,
  retryMapsCensusWebsites,
} from "@/lib/maps/adminApi";
import type {
  MapsCensusCellItem,
  MapsCensusRegionItem,
  MapsCensusRunAdminDetail,
  MapsExportSummaryResponse,
  MapsPlaceDetail,
  MapsPlaceItem,
  ProviderWorkspaceTab,
} from "@/lib/maps/adminTypes";
import { PROVIDER_TAB_FILTERS } from "@/lib/maps/adminTypes";
import { countryFlagEmoji } from "@/lib/maps/countryVisuals";
import { cn } from "@/lib/utils";

const POLL_INTERVAL_MS = 5000;
const TERMINAL_STAGES = new Set([
  "completed",
  "completed_with_warnings",
  "failed",
  "cancelled",
]);
const TERMINAL_STATUSES = new Set([
  "completed",
  "completed_with_warnings",
  "failed",
  "cancelled",
]);

type ConfirmKind = "cancel" | "retry_cells" | "retry_websites" | "retry_enrichment" | null;
type PendingReview = { placeId: string; action: string; label: string } | null;

function formatRuntime(startedAt: string | null, completedAt: string | null): string {
  if (!startedAt) return "—";
  const start = new Date(startedAt).getTime();
  const end = completedAt ? new Date(completedAt).getTime() : Date.now();
  if (!Number.isFinite(start) || !Number.isFinite(end)) return "—";
  const minutes = Math.max(0, Math.floor((end - start) / 60_000));
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const rem = minutes % 60;
  return `${hours}h ${rem}m`;
}

function formatStage(stage: string): string {
  return stage.replace(/_/g, " ");
}

function isActiveCampaign(dashboard: MapsCensusRunAdminDetail | null): boolean {
  if (!dashboard) return false;
  if (TERMINAL_STATUSES.has(dashboard.status) || TERMINAL_STAGES.has(dashboard.current_stage)) {
    return false;
  }
  if (dashboard.overall_status && TERMINAL_STAGES.has(dashboard.overall_status)) {
    return false;
  }
  return dashboard.status === "queued" || dashboard.status === "running";
}

function metricValue(value: unknown): string | number {
  if (value == null) return "—";
  if (typeof value === "number") return value;
  return String(value);
}

function EvidenceBlock({ label, value }: { label: string; value: unknown }) {
  if (value == null) return null;
  return (
    <div className="rounded-lg border border-border bg-muted/30 p-3">
      <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{label}</div>
      <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-words text-xs">
        {typeof value === "string" ? value : JSON.stringify(value, null, 2)}
      </pre>
    </div>
  );
}

export function MapsCampaignAdminPage({ runId }: { runId: string }) {
  const { authHeaders } = useAuth();
  const [dashboard, setDashboard] = useState<MapsCensusRunAdminDetail | null>(null);
  const [regions, setRegions] = useState<MapsCensusRegionItem[]>([]);
  const [cells, setCells] = useState<MapsCensusCellItem[]>([]);
  const [cellsTotal, setCellsTotal] = useState(0);
  const [places, setPlaces] = useState<MapsPlaceItem[]>([]);
  const [placesTotal, setPlacesTotal] = useState(0);
  const [exportSummary, setExportSummary] = useState<MapsExportSummaryResponse | null>(null);
  const [placeSheetOpen, setPlaceSheetOpen] = useState(false);
  const [placeDetailLoading, setPlaceDetailLoading] = useState(false);
  const [selectedPlace, setSelectedPlace] = useState<MapsPlaceDetail | null>(null);
  const [selectedCell, setSelectedCell] = useState<MapsCensusCellItem | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [confirmKind, setConfirmKind] = useState<ConfirmKind>(null);
  const [pendingReview, setPendingReview] = useState<PendingReview>(null);
  const [reviewReason, setReviewReason] = useState("");
  const [providerTab, setProviderTab] = useState<ProviderWorkspaceTab>("eligible");
  const [placeSearch, setPlaceSearch] = useState("");
  const [cellSearch, setCellSearch] = useState("");
  const [cellStatus, setCellStatus] = useState<string>("all");
  const [cellFailedOnly, setCellFailedOnly] = useState(false);
  const [cellCappedOnly, setCellCappedOnly] = useState(false);

  const loadAll = useCallback(async () => {
    const auth = authHeaders();
    if (!auth) return;
    const tabFilters = PROVIDER_TAB_FILTERS[providerTab];
    const [dash, regionResp, cellResp, placeResp, exportResp] = await Promise.all([
      getMapsCensusAdminDashboard(auth, runId),
      listMapsCensusAdminRegions(auth, runId, { limit: 200 }),
      listMapsCensusAdminCells(auth, runId, {
        region: cellSearch || undefined,
        status: cellStatus === "all" ? undefined : cellStatus,
        failed_only: cellFailedOnly,
        capped_only: cellCappedOnly,
        limit: 100,
      }),
      listMapsCensusAdminPlaces(auth, runId, {
        search: placeSearch || undefined,
        ...tabFilters,
        limit: 50,
      }),
      getMapsCensusExportSummary(auth, runId),
    ]);
    setDashboard(dash);
    setRegions(regionResp.items);
    setCells(cellResp.items);
    setCellsTotal(cellResp.meta.total);
    setPlaces(placeResp.items);
    setPlacesTotal(placeResp.meta.total);
    setExportSummary(exportResp);
    setError(null);
  }, [
    authHeaders,
    runId,
    providerTab,
    placeSearch,
    cellSearch,
    cellStatus,
    cellFailedOnly,
    cellCappedOnly,
  ]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void loadAll()
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load campaign admin data");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [loadAll]);

  useEffect(() => {
    if (!isActiveCampaign(dashboard)) return;
    const timer = setInterval(() => {
      void loadAll().catch(() => undefined);
    }, POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [dashboard, loadAll]);

  async function runAction(key: string, fn: () => Promise<unknown>) {
    setActionBusy(key);
    try {
      await fn();
      await loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Action failed");
    } finally {
      setActionBusy(null);
      setConfirmKind(null);
    }
  }

  async function openPlaceDetail(placeId: string) {
    const auth = authHeaders();
    if (!auth) return;
    setPlaceSheetOpen(true);
    setPlaceDetailLoading(true);
    setSelectedPlace(null);
    try {
      const detail = await getMapsCensusAdminPlaceDetail(auth, runId, placeId);
      setSelectedPlace(detail);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load place detail");
      setPlaceSheetOpen(false);
    } finally {
      setPlaceDetailLoading(false);
    }
  }

  async function submitReview() {
    const auth = authHeaders();
    if (!auth || !pendingReview || !reviewReason.trim()) return;
    setActionBusy("review");
    try {
      const detail = await applyMapsPlaceReview(auth, runId, pendingReview.placeId, {
        action: pendingReview.action,
        reason: reviewReason.trim(),
      });
      setSelectedPlace(detail);
      setPendingReview(null);
      setReviewReason("");
      await loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Review action failed");
    } finally {
      setActionBusy(null);
    }
  }

  async function handleExport() {
    const auth = authHeaders();
    if (!auth) return;
    setExporting(true);
    try {
      await downloadMapsCensusExport(auth, runId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Export failed");
    } finally {
      setExporting(false);
    }
  }

  const funnelEntries = useMemo(() => {
    const metrics = dashboard?.funnel_metrics ?? {};
    return Object.entries(metrics).slice(0, 8);
  }, [dashboard?.funnel_metrics]);

  if (loading && !dashboard) return <AdminLoading />;
  if (error && !dashboard) return <AdminError message={error} />;
  if (!dashboard) return <AdminError message="Campaign not found." />;

  const auth = authHeaders();
  const paused = dashboard.campaign_paused;
  const canPause = !paused && dashboard.status !== "cancelled" && dashboard.status !== "failed";
  const canResume = paused;
  const canCancel = dashboard.status !== "cancelled" && dashboard.status !== "failed";

  return (
    <TooltipProvider>
      <div className="mx-auto max-w-7xl px-6 pb-10">
        <div className="sticky top-0 z-30 -mx-6 border-b border-border bg-background/90 px-6 py-4 backdrop-blur">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="min-w-0 space-y-2">
              <Link
                to="/admin/maps"
                className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
              >
                <ArrowLeft className="size-3.5" />
                All campaigns
              </Link>
              <div className="flex flex-wrap items-center gap-3">
                <span className="text-2xl" aria-hidden="true">
                  {countryFlagEmoji(dashboard.country_code)}
                </span>
                <div>
                  <h1 className="font-display text-2xl font-semibold">{dashboard.country_name}</h1>
                  <div className="mt-1 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
                    <MapsRunStatusBadge status={dashboard.status} />
                    <Badge variant="outline" className="capitalize">
                      {formatStage(dashboard.current_stage)}
                    </Badge>
                    {paused && <Badge variant="secondary">Paused</Badge>}
                    <span>Runtime {formatRuntime(dashboard.started_at, dashboard.completed_at)}</span>
                  </div>
                </div>
              </div>
            </div>

            <div className="flex flex-wrap gap-2">
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={!!actionBusy}
                    onClick={() => void loadAll()}
                  >
                    <RefreshCw className={cn("size-4", actionBusy === "refresh" && "animate-spin")} />
                    Refresh
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Reload dashboard data</TooltipContent>
              </Tooltip>
              {canPause && (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={!!actionBusy || !auth}
                  onClick={() =>
                    auth &&
                    void runAction("pause", () => pauseMapsCensusRun(auth, runId))
                  }
                >
                  <Pause className="size-4" />
                  Pause
                </Button>
              )}
              {canResume && (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={!!actionBusy || !auth}
                  onClick={() =>
                    auth &&
                    void runAction("resume", () => resumeMapsCensusRun(auth, runId))
                  }
                >
                  <Play className="size-4" />
                  Resume
                </Button>
              )}
              {canCancel && (
                <Button
                  variant="destructive"
                  size="sm"
                  disabled={!!actionBusy}
                  onClick={() => setConfirmKind("cancel")}
                >
                  <Square className="size-4" />
                  Cancel
                </Button>
              )}
              <Button
                variant="outline"
                size="sm"
                disabled={!!actionBusy}
                onClick={() => setConfirmKind("retry_cells")}
              >
                <RotateCcw className="size-4" />
                Retry failed cells
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={!!actionBusy}
                onClick={() => setConfirmKind("retry_websites")}
              >
                Retry websites
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={!!actionBusy}
                onClick={() => setConfirmKind("retry_enrichment")}
              >
                Retry enrichment
              </Button>
              <Button size="sm" disabled={exporting || !auth} onClick={() => void handleExport()}>
                {exporting ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Download className="size-4" />
                )}
                Export XLSX
              </Button>
            </div>
          </div>
        </div>

        {error && (
          <GlassCard className="mt-4 border-destructive/30 p-4 text-sm text-destructive">
            <div className="flex items-center gap-2">
              <AlertTriangle className="size-4 shrink-0" />
              {error}
            </div>
          </GlassCard>
        )}

        <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-6">
          <StatCard
            label="Cells done"
            value={`${dashboard.cells_completed}/${dashboard.cells_total}`}
            hint={
              dashboard.initial_cells || dashboard.expansion_cells
                ? `seed ${dashboard.initial_cells ?? "—"} · expanded +${dashboard.expansion_cells ?? 0}`
                : undefined
            }
          />
          <StatCard label="Pending cells" value={dashboard.cells_pending} />
          <StatCard label="Failed cells" value={dashboard.cells_failed} hint={`${dashboard.cells_capped} capped`} />
          <StatCard label="Places found" value={dashboard.places_found} />
          <StatCard label="Eligible" value={dashboard.places_eligible} hint={`${dashboard.places_review} review`} />
          <StatCard label="Excluded" value={dashboard.places_excluded} />
          <StatCard label="With website" value={dashboard.places_with_website} />
          <StatCard label="Enriched" value={dashboard.places_enriched} />
          <StatCard
            label="Website retries"
            value={dashboard.website_refresh_attempts}
            hint={`Enrichment ${dashboard.enrichment_refresh_attempts}`}
          />
          <StatCard label="Regions" value={dashboard.regions_total} />
          <StatCard
            label="Discovery"
            value={formatStage(dashboard.discovery_status || "—")}
            hint={dashboard.last_activity_at ? `Last ${formatDt(dashboard.last_activity_at)}` : undefined}
          />
          <StatCard label="Website discovery" value={formatStage(dashboard.website_discovery_status || "—")} />
          <StatCard label="Crawl" value={formatStage(dashboard.crawl_status || "—")} />
          <StatCard label="Classification" value={formatStage(dashboard.classification_status || "—")} />
          <StatCard label="Detail enrichment" value={formatStage(dashboard.detail_enrichment_status || "—")} />
          {funnelEntries.map(([key, value]) => (
            <StatCard key={key} label={key.replace(/_/g, " ")} value={metricValue(value)} />
          ))}
        </div>

        {dashboard.quota_metrics && Object.keys(dashboard.quota_metrics).length > 0 && (
          <GlassCard className="mt-4 p-4">
            <h2 className="text-sm font-semibold">Quota metrics</h2>
            <pre className="mt-2 max-h-32 overflow-auto text-xs text-muted-foreground">
              {JSON.stringify(dashboard.quota_metrics, null, 2)}
            </pre>
          </GlassCard>
        )}

        <Collapsible open={profileOpen} onOpenChange={setProfileOpen} className="mt-6">
          <GlassCard className="overflow-hidden p-0">
            <CollapsibleTrigger className="flex w-full items-center justify-between px-4 py-3 text-left hover:bg-muted/30">
              <div>
                <h2 className="font-semibold">Country profile</h2>
                <p className="text-xs text-muted-foreground">
                  Status: {dashboard.country_profile_status ?? "—"}
                  {dashboard.country_profile_error ? ` · ${dashboard.country_profile_error}` : ""}
                </p>
              </div>
              {profileOpen ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
            </CollapsibleTrigger>
            <CollapsibleContent className="border-t border-border px-4 py-4">
              {dashboard.country_profile ? (
                <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words text-xs">
                  {JSON.stringify(dashboard.country_profile, null, 2)}
                </pre>
              ) : (
                <p className="text-sm text-muted-foreground">No country profile generated yet.</p>
              )}
            </CollapsibleContent>
          </GlassCard>
        </Collapsible>

        <section className="mt-6 space-y-3">
          <h2 className="font-semibold">Region coverage</h2>
          <DataTable
            columns={[
              { key: "region", label: "Region" },
              { key: "cells", label: "Cells" },
              { key: "places", label: "Unique places" },
              { key: "funnel", label: "Funnel" },
              { key: "saturation", label: "Saturation" },
            ]}
            rows={regions.map((region) => ({
              id: region.id,
              cells: {
                region: region.region_name,
                cells: `${region.cells_completed}/${region.cells_planned}`,
                places: region.unique_places_found,
                funnel: (
                  <span className="text-xs">
                    {region.eligible_candidates_found} eligible · {region.review_candidates_found} review ·{" "}
                    {region.unrelated_found} unrelated
                  </span>
                ),
                saturation: (
                  <Badge variant="outline" className="capitalize">
                    {region.saturation_status}
                  </Badge>
                ),
              },
            }))}
            empty="No region metrics yet."
          />
        </section>

        <section className="mt-8 space-y-3">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <h2 className="font-semibold">Search cells ({cellsTotal})</h2>
            <div className="flex flex-wrap gap-2">
              <div className="relative min-w-[12rem]">
                <Search className="absolute left-2.5 top-2.5 size-4 text-muted-foreground" />
                <Input
                  value={cellSearch}
                  onChange={(e) => setCellSearch(e.target.value)}
                  placeholder="Filter by region…"
                  className="pl-9"
                />
              </div>
              <Select value={cellStatus} onValueChange={setCellStatus}>
                <SelectTrigger className="w-[140px]">
                  <SelectValue placeholder="Status" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All statuses</SelectItem>
                  <SelectItem value="pending">Pending</SelectItem>
                  <SelectItem value="in_progress">In progress</SelectItem>
                  <SelectItem value="completed">Completed</SelectItem>
                  <SelectItem value="failed">Failed</SelectItem>
                  <SelectItem value="capped">Capped</SelectItem>
                </SelectContent>
              </Select>
              <Button
                variant={cellFailedOnly ? "secondary" : "outline"}
                size="sm"
                onClick={() => setCellFailedOnly((v) => !v)}
              >
                Failed only
              </Button>
              <Button
                variant={cellCappedOnly ? "secondary" : "outline"}
                size="sm"
                onClick={() => setCellCappedOnly((v) => !v)}
              >
                Capped only
              </Button>
            </div>
          </div>
          <DataTable
            columns={[
              { key: "region", label: "Region" },
              { key: "query", label: "Query" },
              { key: "status", label: "Status" },
              { key: "places", label: "Places" },
              { key: "pages", label: "Pages" },
              { key: "started", label: "Started" },
              { key: "actions", label: "" },
            ]}
            rows={cells.map((cell) => ({
              id: cell.id,
              cells: {
                region: (
                  <div>
                    <div>{cell.region_name}</div>
                    {cell.city_name && (
                      <div className="text-xs text-muted-foreground">{cell.city_name}</div>
                    )}
                  </div>
                ),
                query: (
                  <div className="max-w-xs truncate text-xs" title={cell.query_text}>
                    {cell.query_text}
                  </div>
                ),
                status: (
                  <Badge variant="outline" className="capitalize">
                    {cell.status}
                  </Badge>
                ),
                places: cell.places_found,
                pages: cell.pages_fetched,
                started: formatDt(cell.started_at),
                actions: (
                  <button
                    type="button"
                    className="text-sm font-medium text-primary hover:underline"
                    onClick={() => setSelectedCell(cell)}
                  >
                    Details →
                  </button>
                ),
              },
            }))}
            empty="No cells match the current filters."
          />
        </section>

        <section className="mt-8 space-y-3">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <h2 className="font-semibold">Provider workspace</h2>
            <div className="relative min-w-[14rem]">
              <Search className="absolute left-2.5 top-2.5 size-4 text-muted-foreground" />
              <Input
                value={placeSearch}
                onChange={(e) => setPlaceSearch(e.target.value)}
                placeholder="Search providers…"
                className="pl-9"
              />
            </div>
          </div>

          <Tabs value={providerTab} onValueChange={(v) => setProviderTab(v as ProviderWorkspaceTab)}>
            <TabsList className="flex h-auto flex-wrap">
              <TabsTrigger value="eligible">Eligible</TabsTrigger>
              <TabsTrigger value="review">Needs Review</TabsTrigger>
              <TabsTrigger value="public">Public</TabsTrigger>
              <TabsTrigger value="individuals">Individuals</TabsTrigger>
              <TabsTrigger value="unrelated">Unrelated</TabsTrigger>
              <TabsTrigger value="all">All</TabsTrigger>
            </TabsList>
            <TabsContent value={providerTab} className="mt-4">
              <p className="mb-3 text-xs text-muted-foreground">
                Showing {places.length} of {placesTotal} providers
              </p>
              <DataTable
                columns={[
                  { key: "name", label: "Name" },
                  { key: "location", label: "Location" },
                  { key: "lifecycle", label: "Lifecycle" },
                  { key: "eligibility", label: "Eligibility" },
                  { key: "website", label: "Website" },
                  { key: "enrichment", label: "Enrichment" },
                  { key: "bedCount", label: "Beds" },
                  { key: "actions", label: "" },
                ]}
                rows={places.map((place) => ({
                  id: place.id,
                  cells: {
                    name: (
                      <div>
                        <div className="font-medium">{place.canonical_name}</div>
                        {place.operator_name && (
                          <div className="text-xs text-muted-foreground">{place.operator_name}</div>
                        )}
                      </div>
                    ),
                    location: place.city_name ?? place.region_name ?? "—",
                    lifecycle: (
                      <Badge variant="outline" className="capitalize">
                        {place.lifecycle_status.replace(/_/g, " ")}
                      </Badge>
                    ),
                    eligibility: (
                      <Badge variant="secondary" className="capitalize">
                        {place.client_eligibility}
                      </Badge>
                    ),
                    website: place.official_website ? (
                      <a
                        href={place.official_website}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-1 text-primary hover:underline"
                        onClick={(e) => e.stopPropagation()}
                      >
                        Link <ExternalLink className="size-3" />
                      </a>
                    ) : (
                      "—"
                    ),
                    enrichment: (
                      <span className="capitalize text-xs">{place.enrichment_status}</span>
                    ),
                    bedCount: place.bed_count ? (
                      <span className="text-center text-xs font-medium">{place.bed_count}</span>
                    ) : (
                      <span className="text-center text-xs text-muted-foreground">—</span>
                    ),
                    actions: (
                      <button
                        type="button"
                        className="text-sm font-medium text-primary hover:underline"
                        onClick={() => void openPlaceDetail(place.id)}
                      >
                        Evidence →
                      </button>
                    ),
                  },
                }))}
                empty="No providers in this category."
              />
            </TabsContent>
          </Tabs>
        </section>

        <section className="mt-8 space-y-3">
          <h2 className="font-semibold">Export summary</h2>
          <GlassCard className="p-4">
            {exportSummary ? (
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {Object.entries(exportSummary.sheets).map(([sheet, count]) => (
                  <div
                    key={sheet}
                    className="flex items-center justify-between rounded-lg border border-border px-3 py-2 text-sm"
                  >
                    <span>{sheet}</span>
                    <Badge variant="secondary">{count}</Badge>
                  </div>
                ))}
                <div className="flex items-center justify-between rounded-lg border border-primary/30 bg-primary/5 px-3 py-2 text-sm font-medium sm:col-span-2 lg:col-span-3">
                  <span>Total places in workbook</span>
                  <span>{exportSummary.total_places}</span>
                </div>
              </div>
            ) : (
              <Skeleton className="h-24 w-full" />
            )}
            <Button className="mt-4" disabled={exporting || !auth} onClick={() => void handleExport()}>
              {exporting ? <Loader2 className="size-4 animate-spin" /> : <Download className="size-4" />}
              Download workbook
            </Button>
          </GlassCard>
        </section>

        <Sheet open={!!selectedCell} onOpenChange={(open) => !open && setSelectedCell(null)}>
          <SheetContent className="w-full overflow-y-auto sm:max-w-lg">
            <SheetHeader>
              <SheetTitle>Cell detail</SheetTitle>
              <SheetDescription>{selectedCell?.query_text}</SheetDescription>
            </SheetHeader>
            {selectedCell && (
              <div className="mt-6 space-y-3 text-sm">
                {[
                  ["Region", selectedCell.region_name],
                  ["City", selectedCell.city_name ?? "—"],
                  ["Status", selectedCell.status],
                  ["Query family", selectedCell.query_family ?? "—"],
                  ["Query language", selectedCell.query_language ?? "—"],
                  ["Places found", selectedCell.places_found],
                  ["Pages fetched", selectedCell.pages_fetched],
                  ["Raw results", selectedCell.raw_results_found],
                  ["Unique results", selectedCell.unique_results_found],
                  ["Duplicates", selectedCell.duplicates_found],
                  ["Cap reached", selectedCell.result_cap_reached ? "Yes" : "No"],
                  ["Expansion depth", selectedCell.expansion_depth],
                  ["Attempts", selectedCell.attempt_count],
                  ["Started", formatDt(selectedCell.started_at)],
                  ["Completed", formatDt(selectedCell.completed_at)],
                  ["Error", selectedCell.error_message ?? selectedCell.last_error ?? "—"],
                ].map(([label, value]) => (
                  <div key={label} className="flex justify-between gap-4 border-b border-border pb-2">
                    <span className="text-muted-foreground">{label}</span>
                    <span className="text-right">{String(value)}</span>
                  </div>
                ))}
              </div>
            )}
          </SheetContent>
        </Sheet>

        <Sheet
          open={placeSheetOpen}
          onOpenChange={(open) => {
            setPlaceSheetOpen(open);
            if (!open) {
              setSelectedPlace(null);
              setPendingReview(null);
              setReviewReason("");
              setPlaceDetailLoading(false);
            }
          }}
        >
          <SheetContent className="w-full overflow-y-auto sm:max-w-2xl">
            <SheetHeader>
              <SheetTitle>{selectedPlace?.canonical_name ?? "Provider evidence"}</SheetTitle>
              <SheetDescription>
                {selectedPlace?.formatted_address ?? "Classification and enrichment evidence"}
              </SheetDescription>
            </SheetHeader>
            {placeDetailLoading || !selectedPlace ? (
              <div className="mt-8 flex justify-center">
                <Loader2 className="size-6 animate-spin text-muted-foreground" />
              </div>
            ) : (
              <div className="mt-6 space-y-6">
                <div className="flex flex-wrap gap-2">
                  {[
                    { action: "mark_eligible", label: "Mark eligible" },
                    { action: "mark_review", label: "Needs review" },
                    { action: "mark_public", label: "Public" },
                    { action: "mark_individual", label: "Individual" },
                    { action: "mark_excluded", label: "Exclude" },
                  ].map(({ action, label }) => (
                    <Button
                      key={action}
                      variant="outline"
                      size="sm"
                      onClick={() => setPendingReview({ placeId: selectedPlace.id, action, label })}
                    >
                      {label}
                    </Button>
                  ))}
                </div>

                <div className="grid gap-2 text-sm sm:grid-cols-2">
                  {[
                    ["Lifecycle", selectedPlace.lifecycle_status],
                    ["Eligibility", selectedPlace.client_eligibility],
                    ["Operator", selectedPlace.operator_name ?? "—"],
                    ["Ownership", selectedPlace.ownership_status ?? "—"],
                    ["Facility type", selectedPlace.facility_type ?? "—"],
                    ["Operating status", selectedPlace.operating_status ?? "—"],
                    ["Enrichment", selectedPlace.enrichment_status],
                  ].map(([label, value]) => (
                    <div key={label} className="rounded-lg border border-border px-3 py-2">
                      <div className="text-xs text-muted-foreground">{label}</div>
                      <div className="capitalize">{String(value).replace(/_/g, " ")}</div>
                    </div>
                  ))}
                </div>

                {selectedPlace.classification_evidence && (
                  <div className="space-y-2">
                    <h3 className="text-sm font-semibold">Field evidence</h3>
                    {Object.entries(selectedPlace.classification_evidence).map(([field, evidence]) => (
                      <EvidenceBlock key={field} label={field.replace(/_/g, " ")} value={evidence} />
                    ))}
                  </div>
                )}

                {selectedPlace.enrichment_pages_crawled.length > 0 && (
                  <div>
                    <h3 className="text-sm font-semibold">Crawl pages</h3>
                    <ul className="mt-2 space-y-1 text-xs">
                      {selectedPlace.enrichment_pages_crawled.map((url) => (
                        <li key={url}>
                          <a href={url} target="_blank" rel="noreferrer" className="text-primary hover:underline">
                            {url}
                          </a>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {selectedPlace.enrichment_error_message && (
                  <GlassCard className="border-destructive/30 p-3 text-sm text-destructive">
                    {selectedPlace.enrichment_error_message}
                  </GlassCard>
                )}

                {selectedPlace.review_actions.length > 0 && (
                  <div>
                    <h3 className="text-sm font-semibold">Review history</h3>
                    <ul className="mt-2 space-y-2 text-xs">
                      {selectedPlace.review_actions.map((action) => (
                        <li key={action.id} className="rounded-lg border border-border p-3">
                          <div className="font-medium capitalize">{action.action.replace(/_/g, " ")}</div>
                          <div className="text-muted-foreground">{formatDt(action.created_at)}</div>
                          <div className="mt-1">{action.reason}</div>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            )}
          </SheetContent>
        </Sheet>

        <AlertDialog open={!!confirmKind} onOpenChange={(open) => !open && setConfirmKind(null)}>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>
                {confirmKind === "cancel" && "Cancel campaign?"}
                {confirmKind === "retry_cells" && "Retry all failed cells?"}
                {confirmKind === "retry_websites" && "Retry website discovery?"}
                {confirmKind === "retry_enrichment" && "Retry enrichment?"}
              </AlertDialogTitle>
              <AlertDialogDescription>
                {confirmKind === "cancel" &&
                  "This stops the campaign permanently. Pending work will not resume."}
                {confirmKind === "retry_cells" &&
                  "All failed grid cells will be reset to pending and re-queued."}
                {confirmKind === "retry_websites" &&
                  "Website backfill will be requested for places missing official websites."}
                {confirmKind === "retry_enrichment" &&
                  "Structured enrichment will be re-queued for eligible places."}
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel disabled={!!actionBusy}>Keep as is</AlertDialogCancel>
              <AlertDialogAction
                disabled={!!actionBusy || !auth}
                onClick={(e) => {
                  e.preventDefault();
                  if (!auth || !confirmKind) return;
                  if (confirmKind === "cancel") {
                    void runAction("cancel", () => cancelMapsCensusRun(auth, runId));
                  } else if (confirmKind === "retry_cells") {
                    void runAction("retry_cells", () => retryMapsCensusFailedCells(auth, runId));
                  } else if (confirmKind === "retry_websites") {
                    void runAction("retry_websites", () => retryMapsCensusWebsites(auth, runId));
                  } else if (confirmKind === "retry_enrichment") {
                    void runAction("retry_enrichment", () => retryMapsCensusEnrichment(auth, runId));
                  }
                }}
              >
                {actionBusy ? <Loader2 className="size-4 animate-spin" /> : "Confirm"}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>

        <AlertDialog open={!!pendingReview} onOpenChange={(open) => !open && setPendingReview(null)}>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>{pendingReview?.label ?? "Apply review action"}</AlertDialogTitle>
              <AlertDialogDescription>
                Provide a reason for this manual classification override. It will be stored in the audit trail.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <Textarea
              value={reviewReason}
              onChange={(e) => setReviewReason(e.target.value)}
              placeholder="Reason for this review action…"
              rows={4}
            />
            <AlertDialogFooter>
              <AlertDialogCancel disabled={actionBusy === "review"}>Cancel</AlertDialogCancel>
              <AlertDialogAction
                disabled={!reviewReason.trim() || actionBusy === "review"}
                onClick={(e) => {
                  e.preventDefault();
                  void submitReview();
                }}
              >
                {actionBusy === "review" ? <Loader2 className="size-4 animate-spin" /> : "Apply"}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>
    </TooltipProvider>
  );
}
