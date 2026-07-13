import { Link } from "@tanstack/react-router";
import { ExternalLink } from "lucide-react";
import { GlassCard } from "@/components/cinematic/PageChrome";
import { MissionStatusBadge } from "@/components/scraping/MissionStatusBadge";
import type { ScrapingMissionSummary } from "@/lib/scraping/types";

export function MissionCard({ mission }: { mission: ScrapingMissionSummary }) {
  return (
    <GlassCard className="p-5">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="truncate text-lg font-semibold">{mission.title}</h2>
            <MissionStatusBadge status={mission.status} />
          </div>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">
            {mission.original_prompt.slice(0, 160)}
            {mission.original_prompt.length > 160 ? "..." : ""}
          </p>
          <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span>
              Active blueprint:{" "}
              {mission.active_blueprint_version ? `v${mission.active_blueprint_version}` : "None"}
            </span>
            <span>Updated {new Date(mission.updated_at).toLocaleString()}</span>
          </div>
        </div>
        <Link
          to="/scraping/$missionId"
          params={{ missionId: mission.id }}
          className="inline-flex shrink-0 items-center justify-center gap-2 rounded-xl border border-border px-3 py-2 text-sm font-medium hover:bg-accent"
        >
          Open <ExternalLink className="size-4" />
        </Link>
      </div>
    </GlassCard>
  );
}
