import { Link } from "@tanstack/react-router";
import { Compass, ExternalLink } from "lucide-react";
import { MissionStatusBadge } from "@/components/scraping/MissionStatusBadge";
import { countryLabel } from "@/lib/scraping/countries";
import type { ScrapingMissionSummary } from "@/lib/scraping/types";

export function MissionCard({ mission }: { mission: ScrapingMissionSummary }) {
  const hasCountry = Boolean(mission.country_code && mission.country_name);
  const destination = hasCountry
    ? countryLabel(mission.country_code, mission.country_name)
    : null;

  return (
    <Link
      to="/scraping/$missionId"
      params={{ missionId: mission.id }}
      className="dream-rise group relative block overflow-hidden rounded-[1.5rem] border border-border/90 bg-card/95 p-5 shadow-[0_8px_28px_oklch(0.45_0.04_240/0.06)] transition hover:-translate-y-0.5 hover:border-primary/35 hover:shadow-[0_12px_36px_oklch(0.55_0.1_240/0.12)]"
    >
      <div
        aria-hidden
        className="dream-drift pointer-events-none absolute -right-10 top-0 h-28 w-28 rounded-full blur-3xl opacity-60 transition group-hover:opacity-100"
        style={{ background: "oklch(0.72 0.1 240 / 0.16)" }}
      />
      <div className="relative flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="truncate font-display text-lg tracking-tight text-foreground">
              {mission.title}
            </h2>
            <MissionStatusBadge status={mission.status} />
          </div>
          {destination && (
            <p className="mt-2 inline-flex items-center gap-1.5 text-xs uppercase tracking-[0.18em] text-primary">
              <Compass className="size-3" />
              {destination}
            </p>
          )}
          <p className="mt-2 text-sm leading-6 text-muted-foreground">
            {mission.original_prompt.slice(0, 140)}
            {mission.original_prompt.length > 140 ? "…" : ""}
          </p>
          <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span>
              Chart{" "}
              {mission.active_blueprint_version ? `v${mission.active_blueprint_version}` : "pending"}
            </span>
            <span>Updated {new Date(mission.updated_at).toLocaleString()}</span>
          </div>
        </div>
        <span className="inline-flex shrink-0 items-center justify-center gap-2 rounded-xl border border-border bg-background px-3 py-2 text-sm font-medium transition group-hover:border-primary/40 group-hover:bg-primary/5 group-hover:text-primary">
          Enter flight <ExternalLink className="size-3.5" />
        </span>
      </div>
    </Link>
  );
}
