import { Link } from "@tanstack/react-router";
import { Building2, Grid2x2, MapPin, Search, Trash2 } from "lucide-react";
import { MapsRunStatusBadge } from "@/components/maps/MapsRunStatusBadge";
import { countryFlagEmoji, getFlagColors } from "@/lib/maps/countryVisuals";
import type { MapsCensusRunSummary } from "@/lib/maps/types";
import { cn } from "@/lib/utils";

export function MapsRunCard({
  run,
  onDelete,
}: {
  run: MapsCensusRunSummary;
  onDelete?: (runId: string) => void;
}) {
  const [primary, secondary] = getFlagColors(run.country_code);
  const gradientStyle = run.hero_image_url
    ? undefined
    : { backgroundImage: `linear-gradient(135deg, ${primary}, ${secondary})` };

  return (
    <Link
      to="/maps/$runId"
      params={{ runId: run.id }}
      className="dream-rise group relative block overflow-hidden rounded-[1.5rem] border border-border/90 shadow-[0_8px_28px_oklch(0.45_0.04_240/0.06)] transition hover:-translate-y-0.5 hover:border-primary/35 hover:shadow-[0_12px_36px_oklch(0.55_0.1_240/0.12)]"
    >
      <div className="relative isolate min-h-[9.5rem] overflow-hidden bg-slate-900">
        {run.hero_image_url && (
          <img
            src={run.hero_image_url}
            alt=""
            aria-hidden="true"
            className="absolute inset-0 size-full object-cover"
          />
        )}
        <div
          className={cn(
            "absolute inset-0",
            run.hero_image_url
              ? "bg-gradient-to-t from-slate-950/90 via-slate-950/55 to-slate-950/10"
              : undefined,
          )}
          style={gradientStyle}
        />

        {onDelete && (
          <button
            type="button"
            aria-label={`Delete ${run.country_name} Maps census run`}
            onClick={(event) => {
              event.preventDefault();
              event.stopPropagation();
              if (window.confirm(`Delete the Maps census run for ${run.country_name}?`)) {
                onDelete(run.id);
              }
            }}
            className="absolute right-4 top-4 z-10 rounded-lg bg-black/25 p-1.5 text-white/80 opacity-0 backdrop-blur-sm transition hover:bg-rose-600/80 hover:text-white group-hover:opacity-100 focus:opacity-100"
          >
            <Trash2 className="size-4" />
          </button>
        )}

        <div className="relative flex h-full flex-col justify-between gap-4 p-5">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <span aria-hidden="true" className="text-lg leading-none">
                  {countryFlagEmoji(run.country_code)}
                </span>
                <h2 className="truncate font-display text-lg tracking-tight text-white">
                  {run.country_name}
                </h2>
                <MapsRunStatusBadge status={run.status} />
              </div>
              <p className="mt-1 inline-flex items-center gap-1.5 text-xs uppercase tracking-[0.18em] text-white/70">
                <MapPin className="size-3" />
                {run.country_code}
              </p>
            </div>
            <span className="inline-flex shrink-0 items-center justify-center gap-2 rounded-xl border border-white/25 bg-white/10 px-3 py-2 text-sm font-medium text-white backdrop-blur-sm transition group-hover:bg-white/20">
              View results
            </span>
          </div>

          <div className="flex flex-wrap gap-x-5 gap-y-1.5 text-xs text-white/85">
            <span className="inline-flex items-center gap-1.5">
              <Grid2x2 className="size-3.5" />
              {run.cells_completed}/{run.cells_total} cells
            </span>
            <span className="inline-flex items-center gap-1.5">
              <Search className="size-3.5" />
              {run.places_found} places found
            </span>
            <span className="inline-flex items-center gap-1.5">
              <Building2 className="size-3.5" />
              {run.places_with_website} verified locations
            </span>
            <span className="text-white/60">
              Updated {new Date(run.updated_at).toLocaleString()}
            </span>
          </div>
        </div>
      </div>
      {run.error_message && (
        <p className="border-t border-border/90 bg-card px-5 py-3 text-xs text-rose-600">
          {run.error_message}
        </p>
      )}
    </Link>
  );
}
