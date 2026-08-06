import { Link } from "@tanstack/react-router";
import { ArrowRight, Building2, DollarSign, Grid2x2, MapPin, Search, Trash2 } from "lucide-react";
import { CountryOutline } from "@/components/maps/CountryOutline";
import { MapsRunStatusBadge } from "@/components/maps/MapsRunStatusBadge";
import { formatCost } from "@/lib/cost";
import { countryFlagEmoji, getFlagColors } from "@/lib/maps/countryVisuals";
import type { MapsCensusRunSummary } from "@/lib/maps/types";

export function MapsRunCard({
  run,
  onDelete,
}: {
  run: MapsCensusRunSummary;
  onDelete?: (runId: string) => void;
}) {
  const [primary, secondary] = getFlagColors(run.country_code);

  return (
    <Link
      to="/maps/$runId"
      params={{ runId: run.id }}
      className="dream-rise group relative block overflow-hidden rounded-[1.5rem] border border-white/10 shadow-[0_10px_30px_oklch(0.2_0.04_240/0.25)] transition hover:-translate-y-0.5 hover:border-white/20 hover:shadow-[0_16px_44px_oklch(0.2_0.06_240/0.35)]"
    >
      <div className="relative isolate min-h-[9.5rem] overflow-hidden bg-slate-950">
        {run.hero_image_url ? (
          <>
            <img
              src={run.hero_image_url}
              alt=""
              aria-hidden="true"
              className="absolute inset-0 size-full object-cover transition duration-500 group-hover:scale-[1.03]"
            />
            <div className="absolute inset-0 bg-gradient-to-r from-slate-950/95 via-slate-950/70 to-slate-950/35" />
          </>
        ) : (
          <>
            {/* Flag-tinted accent glows over a dark base — reads well and never washes out. */}
            <div className="absolute inset-0 bg-gradient-to-br from-slate-950 via-slate-900 to-slate-950" />
            <div
              aria-hidden="true"
              className="absolute inset-0 opacity-90"
              style={{
                backgroundImage: `radial-gradient(120% 120% at 88% 10%, ${primary}66 0%, transparent 42%), radial-gradient(120% 120% at 6% 120%, ${secondary}55 0%, transparent 46%)`,
              }}
            />
          </>
        )}

        <CountryOutline
          countryCode={run.country_code}
          className="absolute -right-4 top-1/2 h-[150%] w-auto -translate-y-1/2 opacity-40 transition group-hover:opacity-55"
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
            className="absolute right-4 top-4 z-10 rounded-lg bg-black/30 p-1.5 text-white/80 opacity-0 backdrop-blur-sm transition hover:bg-rose-600/80 hover:text-white group-hover:opacity-100 focus:opacity-100"
          >
            <Trash2 className="size-4" />
          </button>
        )}

        <div className="relative flex h-full flex-col justify-between gap-4 p-5 sm:p-6">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2.5">
                <span aria-hidden="true" className="text-2xl leading-none drop-shadow">
                  {countryFlagEmoji(run.country_code)}
                </span>
                <h2 className="truncate font-display text-xl font-semibold tracking-tight text-white">
                  {run.country_name}
                </h2>
                <MapsRunStatusBadge status={run.status} />
              </div>
              <p className="mt-1.5 inline-flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-[0.2em] text-white/60">
                <MapPin className="size-3" />
                {run.country_code}
              </p>
            </div>
            <span className="inline-flex shrink-0 items-center justify-center gap-1.5 rounded-xl bg-white px-3.5 py-2 text-sm font-semibold text-slate-900 shadow-sm transition group-hover:gap-2.5">
              View results
              <ArrowRight className="size-4 transition group-hover:translate-x-0.5" />
            </span>
          </div>

          <div className="flex flex-wrap items-center gap-2.5 text-xs font-medium text-white">
            <StatPill icon={<Grid2x2 className="size-3.5" />}>
              {run.cells_completed}/{run.cells_total} cells
            </StatPill>
            <StatPill icon={<Search className="size-3.5" />}>
              {run.places_found} places found
            </StatPill>
            <StatPill icon={<Building2 className="size-3.5" />}>
              {run.places_with_website} verified locations
            </StatPill>
            <StatPill icon={<DollarSign className="size-3.5" />}>
              {formatCost(run.total_cost_usd)} spent
            </StatPill>
            <span className="text-white/55">
              Updated {new Date(run.updated_at).toLocaleString()}
            </span>
          </div>
        </div>
      </div>
      {run.error_message && (
        <p className="bg-rose-950/60 px-5 py-3 text-xs text-rose-200">{run.error_message}</p>
      )}
    </Link>
  );
}

function StatPill({ icon, children }: { icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-white/15 bg-white/10 px-2.5 py-1 backdrop-blur-sm">
      {icon}
      {children}
    </span>
  );
}
