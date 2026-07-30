import { Link } from "@tanstack/react-router";
import { MapPin, ExternalLink, Trash2 } from "lucide-react";
import { MapsRunStatusBadge } from "@/components/maps/MapsRunStatusBadge";
import type { MapsCensusRunSummary } from "@/lib/maps/types";

export function MapsRunCard({
  run,
  onDelete,
}: {
  run: MapsCensusRunSummary;
  onDelete?: (runId: string) => void;
}) {
  return (
    <Link
      to="/maps/$runId"
      params={{ runId: run.id }}
      className="dream-rise group relative block overflow-hidden rounded-[1.5rem] border border-border/90 bg-card/95 p-5 shadow-[0_8px_28px_oklch(0.45_0.04_240/0.06)] transition hover:-translate-y-0.5 hover:border-primary/35 hover:shadow-[0_12px_36px_oklch(0.55_0.1_240/0.12)]"
    >
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
          className="absolute right-4 top-4 z-10 rounded-lg p-1.5 text-muted-foreground opacity-0 transition hover:bg-rose-50 hover:text-rose-600 group-hover:opacity-100 focus:opacity-100"
        >
          <Trash2 className="size-4" />
        </button>
      )}
      <div className="relative flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="truncate font-display text-lg tracking-tight text-foreground">
              {run.country_name}
            </h2>
            <MapsRunStatusBadge status={run.status} />
          </div>
          <p className="mt-2 inline-flex items-center gap-1.5 text-xs uppercase tracking-[0.18em] text-primary">
            <MapPin className="size-3" />
            {run.country_code}
          </p>
          <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span>
              {run.cells_completed}/{run.cells_total} cells
            </span>
            <span>{run.places_found} places found</span>
            <span>{run.places_classified_relevant} relevant</span>
            <span>{run.places_with_website} with official website</span>
            <span>Updated {new Date(run.updated_at).toLocaleString()}</span>
          </div>
          {run.error_message && <p className="mt-2 text-xs text-rose-600">{run.error_message}</p>}
        </div>
        <span className="inline-flex shrink-0 items-center justify-center gap-2 rounded-xl border border-border bg-background px-3 py-2 text-sm font-medium transition group-hover:border-primary/40 group-hover:bg-primary/5 group-hover:text-primary">
          View results <ExternalLink className="size-3.5" />
        </span>
      </div>
    </Link>
  );
}
