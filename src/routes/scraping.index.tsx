import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { Plus } from "lucide-react";
import { useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { DreamHeader, DreamPageShell, DreamPanel } from "@/components/scraping/DreamPageShell";
import { MissionCard } from "@/components/scraping/MissionCard";
import { useAuth } from "@/lib/auth";
import { listScrapingMissions } from "@/lib/scraping/api";
import type { ScrapingMissionSummary } from "@/lib/scraping/types";

export const Route = createFileRoute("/scraping/")({
  head: () => ({ meta: [{ title: "Scraping Council - MultiAI" }] }),
  component: ScrapingPage,
});

function ScrapingPage() {
  const { authHeaders } = useAuth();
  const navigate = useNavigate();
  const [missions, setMissions] = useState<ScrapingMissionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const auth = authHeaders();
    if (!auth) {
      void navigate({ to: "/login" });
      return;
    }
    setLoading(true);
    setError(null);
    void listScrapingMissions(auth)
      .then(setMissions)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load missions"))
      .finally(() => setLoading(false));
  }, [authHeaders, navigate]);

  return (
    <AppShell>
      <DreamPageShell>
        <DreamHeader
          eyebrow="03 — Scraping Council"
          title="Missions across the sky"
          description="Each mission is a flight path over one country — chart the blueprint, then watch sources crystallize."
          action={
            <Link
              to="/scraping/new"
              className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-semibold text-primary-foreground shadow-sm hover:bg-primary/90"
            >
              <Plus className="size-4" />
              New mission
            </Link>
          }
        />

        <div className="mt-8 space-y-4">
          {loading && (
            <DreamPanel className="text-sm text-muted-foreground">Loading missions…</DreamPanel>
          )}
          {error && !loading && (
            <DreamPanel className="text-sm text-rose-600">{error}</DreamPanel>
          )}
          {!loading && !error && missions.length === 0 && (
            <DreamPanel tone="amber" className="p-12 text-center">
              <p className="font-display text-2xl text-foreground">No missions yet</p>
              <p className="mx-auto mt-2 max-w-md text-sm text-muted-foreground">
                Name a destination and launch — the council charts the path before anything scrapes.
              </p>
              <Link
                to="/scraping/new"
                className="mt-6 inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-semibold text-primary-foreground"
              >
                <Plus className="size-4" />
                Launch first mission
              </Link>
            </DreamPanel>
          )}
          {!loading &&
            !error &&
            missions.map((mission, index) => (
              <div
                key={mission.id}
                className={`dream-rise-delay-${Math.min(index + 1, 4)}`}
                style={{ animation: "dream-rise 0.7s cubic-bezier(0.22, 1, 0.36, 1) both" }}
              >
                <MissionCard mission={mission} />
              </div>
            ))}
        </div>
      </DreamPageShell>
    </AppShell>
  );
}
