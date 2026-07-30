import { createFileRoute, Link, useNavigate, useParams } from "@tanstack/react-router";
import { ArrowLeft, ExternalLink, Globe, MapPin, Phone } from "lucide-react";
import { useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { DreamHeader, DreamPageShell, DreamPanel } from "@/components/scraping/DreamPageShell";
import { MapsRunStatusBadge } from "@/components/maps/MapsRunStatusBadge";
import { useAuth } from "@/lib/auth";
import { getMapsCensusRun, listMapsCensusPlaces } from "@/lib/maps/api";
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
  const [relevantOnly, setRelevantOnly] = useState(true);
  const [withWebsiteOnly, setWithWebsiteOnly] = useState(false);

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
          listMapsCensusPlaces(auth!, runId, { relevantOnly, withWebsiteOnly }),
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
  }, [authHeaders, navigate, runId, relevantOnly, withWebsiteOnly]);

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
              description={`${run.cells_completed}/${run.cells_total} cells searched · ${run.places_found} places found · ${run.places_classified_relevant} classified relevant · ${run.places_with_website} with an official website.`}
              action={<MapsRunStatusBadge status={run.status} />}
            />
            {run.error_message && (
              <DreamPanel className="mt-6 text-sm text-rose-600">{run.error_message}</DreamPanel>
            )}

            <div className="mt-8 flex flex-wrap items-center gap-3">
              <FilterToggle
                label="Relevant only"
                checked={relevantOnly}
                onChange={setRelevantOnly}
              />
              <FilterToggle
                label="With official website"
                checked={withWebsiteOnly}
                onChange={setWithWebsiteOnly}
              />
            </div>

            <div className="mt-6 space-y-3">
              {places.length === 0 && (
                <DreamPanel className="text-sm text-muted-foreground">
                  {ACTIVE_STATUSES.has(run.status)
                    ? "Census still running — results will appear here as places are found and classified."
                    : "No places match the current filters."}
                </DreamPanel>
              )}
              {places.map((place) => (
                <PlaceRow key={place.id} place={place} />
              ))}
            </div>
          </>
        )}
      </DreamPageShell>
    </AppShell>
  );
}

function FilterToggle({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onChange(!checked)}
      className={
        checked
          ? "rounded-full border border-primary/40 bg-primary/10 px-3.5 py-1.5 text-xs font-medium text-primary"
          : "rounded-full border border-border bg-card px-3.5 py-1.5 text-xs font-medium text-muted-foreground"
      }
    >
      {label}
    </button>
  );
}

function PlaceRow({ place }: { place: MapsPlaceItem }) {
  return (
    <div className="rounded-2xl border border-border/90 bg-card/95 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="truncate font-display text-base text-foreground">
            {place.canonical_name}
          </h3>
          {place.formatted_address && (
            <p className="mt-1 inline-flex items-center gap-1.5 text-xs text-muted-foreground">
              <MapPin className="size-3" />
              {place.formatted_address}
            </p>
          )}
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
            {place.international_phone_number && (
              <span className="inline-flex items-center gap-1.5">
                <Phone className="size-3" />
                {place.international_phone_number}
              </span>
            )}
            {place.official_website && (
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
            )}
          </div>
          {place.relevance_reason && (
            <p className="mt-2 text-xs text-muted-foreground">{place.relevance_reason}</p>
          )}
        </div>
        <span
          className={
            place.is_relevant
              ? "shrink-0 rounded-full border border-teal-300/50 bg-teal-50 px-2.5 py-0.5 text-[10px] font-medium uppercase tracking-[0.14em] text-teal-800"
              : "shrink-0 rounded-full border border-border bg-muted/50 px-2.5 py-0.5 text-[10px] font-medium uppercase tracking-[0.14em] text-muted-foreground"
          }
        >
          {place.is_relevant === null ? "pending" : place.is_relevant ? "relevant" : "not relevant"}
        </span>
      </div>
    </div>
  );
}
