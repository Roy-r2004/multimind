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
      className="dream-rise group relative block overflow-hidden rounded-[1.5rem] border border-white/10 bg-[#0b161c]/80 p-5 shadow-[0_20px_50px_rgba(0,0,0,0.22)] backdrop-blur-md transition hover:-translate-y-0.5 hover:border-[#d4a84b]/40 hover:shadow-[0_28px_60px_rgba(212,168,75,0.12)]"
    >
      <div
        aria-hidden
        className="dream-drift pointer-events-none absolute -right-10 top-0 h-28 w-28 rounded-full blur-3xl opacity-60 transition group-hover:opacity-100"
        style={{ background: "rgba(212,168,75,0.16)" }}
      />
      <div className="relative flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="truncate font-display text-lg font-semibold tracking-tight text-[#f7f1e4]">
              {mission.title}
            </h2>
            <MissionStatusBadge status={mission.status} />
          </div>
          {destination && (
            <p className="mt-2 inline-flex items-center gap-1.5 text-xs uppercase tracking-[0.18em] text-[#d4a84b]/90">
              <Compass className="size-3" />
              {destination}
            </p>
          )}
          <p className="mt-2 text-sm leading-6 text-white/55">
            {mission.original_prompt.slice(0, 140)}
            {mission.original_prompt.length > 140 ? "…" : ""}
          </p>
          <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-white/40">
            <span>
              Chart{" "}
              {mission.active_blueprint_version ? `v${mission.active_blueprint_version}` : "pending"}
            </span>
            <span>Updated {new Date(mission.updated_at).toLocaleString()}</span>
          </div>
        </div>
        <span className="inline-flex shrink-0 items-center justify-center gap-2 rounded-xl border border-white/15 bg-white/5 px-3 py-2 text-sm font-medium text-[#f3e6c4] transition group-hover:border-[#d4a84b]/50 group-hover:bg-[#d4a84b]/10">
          Enter flight <ExternalLink className="size-3.5" />
        </span>
      </div>
    </Link>
  );
}
