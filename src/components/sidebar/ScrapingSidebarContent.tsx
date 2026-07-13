import { Link } from "@tanstack/react-router";
import { ClipboardList, History, Plus } from "lucide-react";
import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import { listScrapingMissions } from "@/lib/scraping/api";
import type { ScrapingMissionSummary } from "@/lib/scraping/types";

export function ScrapingSidebarContent({ onNavigate }: { onNavigate: () => void }) {
  const { authHeaders } = useAuth();
  const [missions, setMissions] = useState<ScrapingMissionSummary[]>([]);

  useEffect(() => {
    const auth = authHeaders();
    if (!auth) return;
    void listScrapingMissions(auth)
      .then(setMissions)
      .catch(() => setMissions([]));
  }, [authHeaders]);

  return (
    <>
      <div className="p-3">
        <Link
          to="/scraping/new"
          onClick={onNavigate}
          className="flex w-full items-center gap-2 rounded-xl bg-primary px-3 py-2.5 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90"
        >
          <Plus className="size-4" /> New Scraping Mission
        </Link>
      </div>
      <div className="mt-4 flex-1 overflow-hidden px-3">
        <div className="flex items-center gap-2 px-2 text-[10px] font-semibold uppercase tracking-[0.2em] text-muted-foreground">
          <History className="size-3.5" /> Recent Scraping Missions
        </div>
        <div className="mt-2 max-h-[38vh] space-y-0.5 overflow-y-auto">
          {missions.length === 0 ? (
            <p className="px-2 py-3 text-xs text-muted-foreground">No scraping missions yet</p>
          ) : (
            missions.map((mission) => (
              <Link
                key={mission.id}
                to="/scraping/$missionId"
                params={{ missionId: mission.id }}
                onClick={onNavigate}
                className="flex items-start gap-2 rounded-lg px-3 py-2 text-sm text-sidebar-foreground/85 hover:bg-accent"
              >
                <ClipboardList className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                <span className="min-w-0">
                  <span className="block truncate">{mission.title}</span>
                  <span className="block truncate text-[10px] text-muted-foreground">
                    {mission.status}
                  </span>
                </span>
              </Link>
            ))
          )}
        </div>
      </div>
    </>
  );
}
