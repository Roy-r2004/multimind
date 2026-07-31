import { createFileRoute, Link, useNavigate, useParams } from "@tanstack/react-router";
import {
  ArrowLeft,
  Building2,
  Download,
  ExternalLink,
  Grid2x2,
  Loader2,
  Search,
  Sparkles,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { DreamPageShell, DreamPanel } from "@/components/scraping/DreamPageShell";
import { CountryOutline } from "@/components/maps/CountryOutline";
import { MapsRunStatusBadge } from "@/components/maps/MapsRunStatusBadge";
import { countryFlagEmoji, getFlagColors } from "@/lib/maps/countryVisuals";
import {
  EXPORT_COLUMNS,
  placeToExportRow,
  sortPlacesForExport,
} from "@/lib/maps/exportDisplay";
import { cn } from "@/lib/utils";
import { useAuth } from "@/lib/auth";
import {
  downloadMapsCensusExport,
  enrichMapsCensusRun,
  getMapsCensusRun,
  listMapsCensusCells,
  listMapsCensusPlaces,
  refreshMapsCensusWebsites,
} from "@/lib/maps/api";
import type { MapsCensusCellItem, MapsCensusRunDetail, MapsPlaceItem } from "@/lib/maps/types";

const ACTIVE_STATUSES = new Set(["queued", "running"]);
const POLL_INTERVAL_MS = 5000;

export const Route = createFileRoute("/maps/$runId")({
  head: () => ({ meta: [{ title: "Maps Census Run - MultiAI" }] }),
  component: MapsRunDetailPage,
});

function MapsRunDetailPage() {
  const { runId } = useParams({ from: "/maps/$runId" });
  const { authHeaders } = useAuth();
  const navigate = useNavigate();
  const [run, setRun] = useState<MapsCensusRunDetail | null>(null);
  const [places, setPlaces] = useState<MapsPlaceItem[]>([]);
  const [searchCells, setSearchCells] = useState<MapsCensusCellItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [enriching, setEnriching] = useState(false);
  const [pollTick, setPollTick] = useState(0);
  const [showExportOnly, setShowExportOnly] = useState(false);

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
        const [runDetail, placeItems, cellItems] = await Promise.all([
          getMapsCensusRun(auth!, runId),
          listMapsCensusPlaces(auth!, runId, { relevantOnly: true }),
          listMapsCensusCells(auth!, runId),
        ]);
        if (cancelled) return;
        setRun(runDetail);
        setPlaces(placeItems);
        setSearchCells(cellItems);
        setError(null);
        if (ACTIVE_STATUSES.has(runDetail.status)) {
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
  }, [authHeaders, navigate, runId, pollTick]);

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

  async function handleDownloadExport() {
    const auth = authHeaders();
    if (!auth || run?.status !== "completed") return;
    setExporting(true);
    setError(null);
    try {
      await downloadMapsCensusExport(auth, runId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to download CSV export");
    } finally {
      setExporting(false);
    }
  }

  async function handleEnrichWebsites() {
    const auth = authHeaders();
    if (!auth) return;
    setEnriching(true);
    setError(null);
    try {
      const updated = await enrichMapsCensusRun(auth, runId);
      setRun(updated);
      setPollTick((tick) => tick + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start website enrichment");
    } finally {
      setEnriching(false);
    }
  }

  const exportRows = useMemo(() => {
    if (!run) return [];
    const filtered = showExportOnly ? places.filter((place) => place.export_eligible) : places;
    return sortPlacesForExport(filtered).map((place) => placeToExportRow(place, run.country_name));
  }, [places, run, showExportOnly]);

  const exportEligibleCount = places.filter((place) => place.export_eligible).length;

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
              exporting={exporting}
              enriching={enriching}
              onFindMissingWebsites={() => void handleFindMissingWebsites()}
              onDownloadExport={() => void handleDownloadExport()}
              onEnrichWebsites={() => void handleEnrichWebsites()}
            />
            {run.error_message && (
              <DreamPanel className="mt-6 text-sm text-rose-600">{run.error_message}</DreamPanel>
            )}

            <div className="mt-8 grid grid-cols-2 gap-4 sm:grid-cols-4">
              <MapsStatCard
                icon={<Building2 className="size-4" />}
                label="Relevant facilities"
                value={places.length}
                tone="teal"
                emphasize
              />
              <MapsStatCard
                icon={<Download className="size-4" />}
                label="Export-ready rows"
                value={exportEligibleCount}
                tone="sky"
              />
              <MapsStatCard
                icon={<Search className="size-4" />}
                label="Places scanned"
                value={run.places_found}
                tone="amber"
              />
              <MapsStatCard
                icon={<Grid2x2 className="size-4" />}
                label="Search queries"
                value={searchCells.length}
                tone="violet"
              />
            </div>

            <SearchKeywordsTable cells={searchCells} isRunning={ACTIVE_STATUSES.has(run.status)} />

            <FacilitiesExportTable
              rows={exportRows}
              isRunning={ACTIVE_STATUSES.has(run.status)}
              showExportOnly={showExportOnly}
              onToggleExportOnly={() => setShowExportOnly((value) => !value)}
              exportEligibleCount={exportEligibleCount}
              totalCount={places.length}
            />
          </>
        )}
      </DreamPageShell>
    </AppShell>
  );
}

function SearchKeywordsTable({
  cells,
  isRunning,
}: {
  cells: MapsCensusCellItem[];
  isRunning: boolean;
}) {
  const [open, setOpen] = useState(true);

  return (
    <DreamPanel className="mt-8 p-0 overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center justify-between gap-3 px-5 py-4 text-left transition hover:bg-muted/30"
      >
        <div>
          <h2 className="font-display text-base font-semibold text-foreground">Search keywords</h2>
          <p className="mt-0.5 text-sm text-muted-foreground">
            {cells.length} Google Places queries used for this run
          </p>
        </div>
        <span className="text-sm text-muted-foreground">{open ? "Hide" : "Show"}</span>
      </button>

      {open && (
        <div className="max-h-72 overflow-auto border-t border-border/80">
          <table className="w-full min-w-[640px] border-collapse text-sm">
            <thead className="sticky top-0 z-10 bg-muted/90 backdrop-blur-sm">
              <tr className="border-b border-border/80 text-left text-[11px] uppercase tracking-[0.08em] text-muted-foreground">
                <th className="px-4 py-2.5 font-semibold w-12">#</th>
                <th className="px-4 py-2.5 font-semibold w-36">City</th>
                <th className="px-4 py-2.5 font-semibold w-40">Region</th>
                <th className="px-4 py-2.5 font-semibold">Keyword</th>
                <th className="px-4 py-2.5 font-semibold w-24 text-right">Found</th>
              </tr>
            </thead>
            <tbody>
              {cells.length === 0 ? (
                <tr>
                  <td
                    colSpan={5}
                    className="px-4 py-4 text-sm text-muted-foreground"
                  >
                    {isRunning
                      ? "Planning search grid… keyword rows will appear here once the run starts."
                      : "No search keywords recorded."}
                  </td>
                </tr>
              ) : (
                cells.map((cell, index) => (
                  <tr
                    key={cell.id}
                    className="border-b border-border/50 odd:bg-background even:bg-muted/20"
                  >
                    <td className="px-4 py-2 text-muted-foreground tabular-nums">{index + 1}</td>
                    <td className="px-4 py-2 text-foreground">{cell.city_name || "—"}</td>
                    <td className="px-4 py-2 text-foreground">{cell.region_name}</td>
                    <td className="px-4 py-2 font-mono text-[13px] text-foreground">{cell.query_text}</td>
                    <td className="px-4 py-2 text-right tabular-nums text-muted-foreground">
                      {cell.places_found}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      )}
    </DreamPanel>
  );
}

function FacilitiesExportTable({
  rows,
  isRunning,
  showExportOnly,
  onToggleExportOnly,
  exportEligibleCount,
  totalCount,
}: {
  rows: ReturnType<typeof placeToExportRow>[];
  isRunning: boolean;
  showExportOnly: boolean;
  onToggleExportOnly: () => void;
  exportEligibleCount: number;
  totalCount: number;
}) {
  return (
    <DreamPanel className="mt-8 p-0 overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border/80 px-5 py-4">
        <div>
          <h2 className="font-display text-base font-semibold text-foreground">
            Rehabilitation facilities
          </h2>
          <p className="mt-0.5 text-sm text-muted-foreground">
            All 7 export columns are always shown. Addictions, languages, and price use placeholders
            until website enrichment fills them in.
          </p>
        </div>
        <label className="inline-flex cursor-pointer items-center gap-2 text-sm text-muted-foreground">
          <input
            type="checkbox"
            checked={showExportOnly}
            onChange={onToggleExportOnly}
            className="size-4 rounded border-border"
          />
          Export-ready only ({exportEligibleCount}/{totalCount})
        </label>
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
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td
                  colSpan={EXPORT_COLUMNS.length}
                  className="px-5 py-8 text-sm text-muted-foreground"
                >
                  {isRunning
                    ? "Census still running — facility rows will appear here as they are classified."
                    : showExportOnly
                      ? "No export-ready rows yet. Turn off the filter or run website enrichment."
                      : "No relevant rehabilitation facilities were confirmed for this country."}
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
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </DreamPanel>
  );
}

function ExportTableCell({ column, value }: { column: string; value: string }) {
  const isPlaceholder = value === "Not Specified" || value === "Contact for pricing";
  const isLink = column === "Website" && !isPlaceholder;

  return (
    <td className="min-w-[8rem] max-w-[18rem] px-3 py-2.5 align-top text-foreground">
      {isLink ? (
        <a
          href={value}
          target="_blank"
          rel="noreferrer"
          className="inline-flex max-w-full items-start gap-1 text-primary hover:underline"
        >
          <span className="break-all">{value}</span>
          <ExternalLink className="mt-0.5 size-3 shrink-0" />
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
  exporting,
  enriching,
  onFindMissingWebsites,
  onDownloadExport,
  onEnrichWebsites,
}: {
  run: MapsCensusRunDetail;
  refreshing: boolean;
  exporting: boolean;
  enriching: boolean;
  onFindMissingWebsites: () => void;
  onDownloadExport: () => void;
  onEnrichWebsites: () => void;
}) {
  const [primary, secondary] = getFlagColors(run.country_code);
  const showFindMissingWebsites =
    run.status === "completed" && run.places_classified_relevant > run.places_with_website;
  const showEnrichWebsites =
    run.status === "completed" &&
    run.places_with_website > 0 &&
    run.places_enriched < run.places_with_website;

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
              Inpatient addiction rehab census — search keywords and export columns in spreadsheet
              view.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            {run.status === "completed" && (
              <button
                type="button"
                onClick={onDownloadExport}
                disabled={exporting}
                className="inline-flex items-center gap-2 rounded-xl border border-white/20 bg-white/10 px-3.5 py-2 text-xs font-semibold text-white backdrop-blur-sm transition hover:bg-white/20 disabled:opacity-50"
              >
                {exporting ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <Download className="size-3.5" />
                )}
                Download CSV
              </button>
            )}
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
                Enrich from websites
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
}: {
  icon: React.ReactNode;
  label: string;
  value: string | number;
  tone: "teal" | "sky" | "amber" | "violet";
  emphasize?: boolean;
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
      )}
    >
      <span className={cn("grid size-9 place-items-center rounded-full", tones[tone])}>
        {icon}
      </span>
      <div className="mt-3 font-display text-2xl font-semibold tracking-tight text-foreground">
        {value}
      </div>
      <div className="mt-0.5 text-[11px] uppercase tracking-[0.1em] text-muted-foreground">
        {label}
      </div>
    </div>
  );
}
