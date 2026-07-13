import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { Plus } from "lucide-react";
import { useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { GlassCard, PageHeader } from "@/components/cinematic/PageChrome";
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
      <div className="mx-auto max-w-5xl px-6 py-10">
        <PageHeader
          eyebrow="Workspace"
          title="Scraping Council"
          description="Create scraping missions, generate structured blueprints, and review them before approval."
          action={
            <Link
              to="/scraping/new"
              className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90"
            >
              <Plus className="size-4" />
              New Scraping Mission
            </Link>
          }
        />

        <div className="mt-8 space-y-4">
          {loading && (
            <GlassCard className="p-8 text-sm text-muted-foreground">Loading missions...</GlassCard>
          )}
          {error && !loading && (
            <GlassCard className="p-8 text-sm text-destructive">{error}</GlassCard>
          )}
          {!loading && !error && missions.length === 0 && (
            <GlassCard className="p-12 text-center text-sm text-muted-foreground">
              No scraping missions yet.
            </GlassCard>
          )}
          {!loading &&
            !error &&
            missions.map((mission) => <MissionCard key={mission.id} mission={mission} />)}
        </div>
      </div>
    </AppShell>
  );
}
