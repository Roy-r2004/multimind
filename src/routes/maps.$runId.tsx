import { createFileRoute, Link, useNavigate, useParams } from "@tanstack/react-router";
import {
  ArrowLeft,
  Building2,
  Download,
  ExternalLink,
  Globe,
  Grid2x2,
  Layers,
  Loader2,
  MapPin,
  Phone,
  Search,
  Sparkles,
} from "lucide-react";
import { useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { DreamPageShell, DreamPanel } from "@/components/scraping/DreamPageShell";
import { CountryOutline } from "@/components/maps/CountryOutline";
import { MapsPlacePhoto } from "@/components/maps/MapsPlacePhoto";
import { MapsRunStatusBadge } from "@/components/maps/MapsRunStatusBadge";
import { countryFlagEmoji, getFlagColors } from "@/lib/maps/countryVisuals";
import { cn } from "@/lib/utils";
import { useAuth } from "@/lib/auth";
import { downloadMapsCensusExport, enrichMapsCensusRun, getMapsCensusRun, listMapsCensusCells, listMapsCensusPlaces, refreshMapsCensusWebsites } from "@/lib/maps/api";
import { groupVerifiedPlaces, type PlaceGroup } from "@/lib/maps/groupPlaces";
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
          listMapsCensusPlaces(auth!, runId, { relevantOnly: true, withWebsiteOnly: true }),
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

  const groupedPlaces = groupVerifiedPlaces(places);

  return (
    <AppShell>
      <DreamPageShell maxWidth="max-w-6xl">
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
                label="Verified rehab centers"
                value={groupedPlaces.length}
                tone="teal"
                emphasize
              />
              <MapsStatCard
                icon={<Globe className="size-4" />}
                label="Verified locations"
                value={places.length}
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
                label="Cells searched"
                value={`${run.cells_completed}/${run.cells_total}`}
                tone="violet"
              />
            </div>

            <SearchKeywordsPanel cells={searchCells} isRunning={ACTIVE_STATUSES.has(run.status)} />

            <div className="mt-10 space-y-3">
              {places.length === 0 && (
                <DreamPanel className="text-sm text-muted-foreground">
                  {ACTIVE_STATUSES.has(run.status)
                    ? "Census still running — rehab facilities will appear here as they're found and verified."
                    : "No rehab facilities were confirmed for this country."}
                </DreamPanel>
              )}
              {groupedPlaces.map((group) =>
                group.places.length > 1 ? (
                  <GroupedPlaceCard key={group.key} runId={run.id} group={group} />
                ) : (
                  <PlaceRow key={group.places[0].id} runId={run.id} place={group.places[0]} />
                ),
              )}
            </div>
          </>
        )}
      </DreamPageShell>
    </AppShell>
  );
}

function SearchKeywordsPanel({
  cells,
  isRunning,
}: {
  cells: MapsCensusCellItem[];
  isRunning: boolean;
}) {
  const grouped = groupSearchCells(cells);

  return (
    <DreamPanel className="mt-8">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-muted-foreground">
            Search grid
          </p>
          <h2 className="mt-1 font-display text-lg font-semibold text-foreground">
            Keywords searched
          </h2>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            LLM-planned Google Places queries for this country — English and local-language
            inpatient addiction rehab terms, scoped by city.
          </p>
        </div>
        <span className="rounded-full border border-border/80 bg-muted/40 px-3 py-1 text-xs text-muted-foreground">
          {cells.length} {cells.length === 1 ? "query" : "queries"}
        </span>
      </div>

      {cells.length === 0 && (
        <p className="mt-4 text-sm text-muted-foreground">
          {isRunning
            ? "Planning search grid… keywords will appear here once the run starts."
            : "No search keywords were recorded for this run."}
        </p>
      )}

      {grouped.length > 0 && (
        <div className="mt-5 space-y-4">
          {grouped.map((group) => (
            <div
              key={group.key}
              className="rounded-xl border border-border/80 bg-background/60 p-4"
            >
              <div className="flex flex-wrap items-center gap-2">
                <MapPin className="size-3.5 text-primary" />
                <h3 className="font-medium text-foreground">{group.label}</h3>
                <span className="text-xs text-muted-foreground">
                  {group.cells.length} {group.cells.length === 1 ? "query" : "queries"}
                </span>
              </div>
              <ul className="mt-3 space-y-2">
                {group.cells.map((cell) => (
                  <li
                    key={cell.id}
                    className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border/60 bg-card/80 px-3 py-2"
                  >
                    <code className="text-sm text-foreground">{cell.query_text}</code>
                    <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.08em]">
                      <SearchCellStatusBadge status={cell.status} />
                      <span className="text-muted-foreground">
                        {cell.places_found} found
                      </span>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </DreamPanel>
  );
}

function SearchCellStatusBadge({ status }: { status: MapsCensusCellItem["status"] }) {
  const styles: Record<MapsCensusCellItem["status"], string> = {
    pending: "bg-muted text-muted-foreground",
    in_progress: "bg-amber-100 text-amber-800",
    completed: "bg-teal-100 text-teal-800",
    failed: "bg-rose-100 text-rose-700",
  };
  const labels: Record<MapsCensusCellItem["status"], string> = {
    pending: "Pending",
    in_progress: "Running",
    completed: "Done",
    failed: "Failed",
  };
  return (
    <span className={cn("rounded-full px-2 py-0.5 font-medium", styles[status])}>
      {labels[status]}
    </span>
  );
}

function groupSearchCells(cells: MapsCensusCellItem[]) {
  const byKey = new Map<string, { key: string; label: string; cells: MapsCensusCellItem[] }>();
  for (const cell of cells) {
    const city = cell.city_name?.trim() || "Region-wide";
    const key = `${cell.region_name}::${city}`;
    const label = city === "Region-wide" ? cell.region_name : `${city}, ${cell.region_name}`;
    const existing = byKey.get(key);
    if (existing) {
      existing.cells.push(cell);
    } else {
      byKey.set(key, { key, label, cells: [cell] });
    }
  }
  return [...byKey.values()];
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
              Non-government inpatient addiction rehab facilities from Google Places, classified by
              AI, with CSV export and searchable keyword grid.
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

function PlaceRow({ runId, place }: { runId: string; place: MapsPlaceItem }) {
  return (
    <div className="flex gap-3 rounded-2xl border border-border/90 bg-card/95 p-4 transition hover:border-primary/30">
      <MapsPlacePhoto
        runId={runId}
        placeId={place.id}
        hasPhoto={place.has_photo}
        alt={place.canonical_name}
        className="size-14"
      />
      <div className="min-w-0 flex-1">
        <h3 className="truncate font-display text-base text-foreground">{place.canonical_name}</h3>
        <PlaceLocationDetails place={place} />
      </div>
    </div>
  );
}

function GroupedPlaceCard({ runId, group }: { runId: string; group: PlaceGroup }) {
  const websites = [
    ...new Set(
      group.places
        .map((place) => place.official_website)
        .filter((url): url is string => Boolean(url)),
    ),
  ];
  const sharedWebsite = websites.length === 1 ? websites[0] : null;
  const coverPlace = group.places.find((place) => place.has_photo) ?? group.places[0];

  return (
    <div className="flex gap-3 rounded-2xl border border-border/90 bg-card/95 p-4 transition hover:border-primary/30">
      <MapsPlacePhoto
        runId={runId}
        placeId={coverPlace.id}
        hasPhoto={coverPlace.has_photo}
        alt={group.name}
        className="size-14"
      />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="truncate font-display text-base text-foreground">{group.name}</h3>
          <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-primary/25 bg-primary/10 px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.1em] text-primary">
            <Layers className="size-3" />
            {group.places.length} locations
          </span>
        </div>
        {sharedWebsite ? (
          <a
            href={sharedWebsite}
            target="_blank"
            rel="noreferrer"
            className="mt-2 inline-flex items-center gap-1.5 text-xs text-primary hover:underline"
          >
            <Globe className="size-3" />
            {sharedWebsite}
            <ExternalLink className="size-3" />
          </a>
        ) : null}
        <div className="mt-3 space-y-3 divide-y divide-border/70">
          {group.places.map((place) => (
            <div key={place.id} className="pt-3 first:mt-0 first:border-0 first:pt-0">
              <PlaceLocationDetails place={place} hideWebsite={Boolean(sharedWebsite)} />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function PlaceLocationDetails({
  place,
  hideWebsite = false,
}: {
  place: MapsPlaceItem;
  hideWebsite?: boolean;
}) {
  return (
    <>
      {place.formatted_address && (
        <p className="mt-1 inline-flex items-center gap-1.5 text-xs text-muted-foreground">
          <MapPin className="size-3 shrink-0" />
          {place.formatted_address}
        </p>
      )}
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1.5 text-xs">
        {place.international_phone_number ? (
          <span className="inline-flex items-center gap-1.5 text-muted-foreground">
            <Phone className="size-3" />
            {place.international_phone_number}
          </span>
        ) : (
          <span className="text-muted-foreground/60">no phone found</span>
        )}
        {!hideWebsite &&
          (place.official_website ? (
            <a
              href={place.official_website}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1.5 text-primary hover:underline"
            >
              <Globe className="size-3" />
              {place.official_website}
              <ExternalLink className="size-3" />
            </a>
          ) : (
            <span className="text-muted-foreground/60">no verified official website found</span>
          ))}
      </div>
    </>
  );
}
