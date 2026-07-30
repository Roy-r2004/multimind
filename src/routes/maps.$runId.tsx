import { createFileRoute, Link, useNavigate, useParams } from "@tanstack/react-router";
import {
  ArrowLeft,
  Building2,
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
import { DreamHeader, DreamPageShell, DreamPanel } from "@/components/scraping/DreamPageShell";
import { MapsRunStatusBadge } from "@/components/maps/MapsRunStatusBadge";
import { cn } from "@/lib/utils";
import { useAuth } from "@/lib/auth";
import { getMapsCensusRun, listMapsCensusPlaces, refreshMapsCensusWebsites } from "@/lib/maps/api";
import type { MapsCensusRunDetail, MapsPlaceItem } from "@/lib/maps/types";

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
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
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
        const [runDetail, placeItems] = await Promise.all([
          getMapsCensusRun(auth!, runId),
          listMapsCensusPlaces(auth!, runId, { relevantOnly: true }),
        ]);
        if (cancelled) return;
        setRun(runDetail);
        setPlaces(placeItems);
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

  return (
    <AppShell>
      <DreamPageShell maxWidth="max-w-6xl">
        <Link
          to="/maps"
          className="mb-8 inline-flex w-fit items-center gap-2 text-sm text-muted-foreground transition hover:text-primary"
        >
          <ArrowLeft className="size-3.5" />
          All Maps census runs
        </Link>

        {loading && !run && (
          <DreamPanel className="text-sm text-muted-foreground">Loading run…</DreamPanel>
        )}
        {error && <DreamPanel className="mb-6 text-sm text-rose-600">{error}</DreamPanel>}

        {run && (
          <>
            <DreamHeader
              eyebrow="Maps Census"
              title={run.country_name}
              description="Rehabilitation, addiction, and psychiatric facilities discovered via Google Places and verified by AI."
              action={
                <div className="flex items-center gap-3">
                  {run.status === "completed" &&
                    run.places_classified_relevant > run.places_with_website && (
                      <button
                        type="button"
                        onClick={() => void handleFindMissingWebsites()}
                        disabled={refreshing}
                        className="inline-flex items-center gap-2 rounded-xl bg-primary px-3.5 py-2 text-xs font-semibold text-primary-foreground shadow-sm transition hover:bg-primary/90 disabled:opacity-50"
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
              }
            />
            {run.error_message && (
              <DreamPanel className="mt-6 text-sm text-rose-600">{run.error_message}</DreamPanel>
            )}

            <div className="mt-8 grid grid-cols-2 gap-4 sm:grid-cols-4">
              <MapsStatCard
                icon={<Building2 className="size-4" />}
                label="Rehab facilities"
                value={run.places_classified_relevant}
                tone="teal"
                emphasize
              />
              <MapsStatCard
                icon={<Globe className="size-4" />}
                label="With verified website"
                value={run.places_with_website}
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

            <div className="mt-10 space-y-3">
              {places.length === 0 && (
                <DreamPanel className="text-sm text-muted-foreground">
                  {ACTIVE_STATUSES.has(run.status)
                    ? "Census still running — rehab facilities will appear here as they're found and verified."
                    : "No rehab facilities were confirmed for this country."}
                </DreamPanel>
              )}
              {groupPlacesByName(places).map((group) =>
                group.places.length > 1 ? (
                  <GroupedPlaceCard key={group.key} group={group} />
                ) : (
                  <PlaceRow key={group.places[0].id} place={group.places[0]} />
                ),
              )}
            </div>
          </>
        )}
      </DreamPageShell>
    </AppShell>
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
      <div className="flex items-start gap-3">
        <span className={cn("grid size-9 shrink-0 place-items-center rounded-full", tones[tone])}>
          {icon}
        </span>
        <div className="min-w-0">
          <div className="font-display text-2xl font-semibold tracking-tight text-foreground">
            {value}
          </div>
          <div className="mt-0.5 text-[11px] uppercase tracking-[0.1em] text-muted-foreground">
            {label}
          </div>
        </div>
      </div>
    </div>
  );
}

interface PlaceGroup {
  key: string;
  name: string;
  places: MapsPlaceItem[];
}

function groupPlacesByName(places: MapsPlaceItem[]): PlaceGroup[] {
  const groups = new Map<string, PlaceGroup>();
  for (const place of places) {
    const key = place.canonical_name.trim().toLowerCase();
    const existing = groups.get(key);
    if (existing) {
      existing.places.push(place);
    } else {
      groups.set(key, { key, name: place.canonical_name, places: [place] });
    }
  }
  return Array.from(groups.values());
}

function PlaceRow({ place }: { place: MapsPlaceItem }) {
  return (
    <div className="rounded-2xl border border-border/90 bg-card/95 p-4 transition hover:border-primary/30">
      <h3 className="truncate font-display text-base text-foreground">{place.canonical_name}</h3>
      <PlaceLocationDetails place={place} />
    </div>
  );
}

function GroupedPlaceCard({ group }: { group: PlaceGroup }) {
  return (
    <div className="rounded-2xl border border-border/90 bg-card/95 p-4 transition hover:border-primary/30">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="truncate font-display text-base text-foreground">{group.name}</h3>
        <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-primary/25 bg-primary/10 px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.1em] text-primary">
          <Layers className="size-3" />
          {group.places.length} locations
        </span>
      </div>
      <div className="mt-3 space-y-3 divide-y divide-border/70">
        {group.places.map((place) => (
          <div key={place.id} className="pt-3 first:mt-0 first:border-0 first:pt-0">
            <PlaceLocationDetails place={place} />
          </div>
        ))}
      </div>
    </div>
  );
}

function PlaceLocationDetails({ place }: { place: MapsPlaceItem }) {
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
        {place.official_website ? (
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
        )}
      </div>
    </>
  );
}
