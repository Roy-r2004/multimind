import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { GlassCard, PageHeader } from "@/components/cinematic/PageChrome";
import { MissionStatusBadge } from "@/components/scraping/MissionStatusBadge";
import { useAuth } from "@/lib/auth";
import { getScrapingMission } from "@/lib/scraping/api";
import type { ScrapingMissionDetail } from "@/lib/scraping/types";

export const Route = createFileRoute("/scraping/$missionId/")({
  head: () => ({ meta: [{ title: "Scraping Mission - MultiAI" }] }),
  component: ScrapingMissionPage,
});

function ScrapingMissionPage() {
  const { missionId } = Route.useParams();
  const { authHeaders } = useAuth();
  const navigate = useNavigate();
  const [mission, setMission] = useState<ScrapingMissionDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const auth = authHeaders();
    if (!auth) {
      void navigate({ to: "/login" });
      return;
    }
    void getScrapingMission(auth, missionId)
      .then(setMission)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load mission"));
  }, [authHeaders, missionId, navigate]);

  return (
    <AppShell>
      <div className="mx-auto max-w-4xl px-6 py-10">
        <PageHeader
          eyebrow="Scraping Council"
          title={mission?.title ?? "Scraping Mission"}
          description="Mission overview and blueprint status."
          action={
            <Link
              to="/scraping/$missionId/blueprint"
              params={{ missionId }}
              className="rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground"
            >
              Open Blueprint
            </Link>
          }
        />
        {error && <GlassCard className="mt-8 p-8 text-sm text-destructive">{error}</GlassCard>}
        {!error && !mission && (
          <GlassCard className="mt-8 p-8 text-sm text-muted-foreground">
            Loading mission...
          </GlassCard>
        )}
        {mission && (
          <GlassCard className="mt-8 p-6">
            <dl className="grid gap-5 text-sm md:grid-cols-2">
              <div className="md:col-span-2">
                <dt className="font-medium">Original prompt</dt>
                <dd className="mt-1 whitespace-pre-wrap text-muted-foreground">
                  {mission.original_prompt}
                </dd>
              </div>
              <div>
                <dt className="font-medium">Mission status</dt>
                <dd className="mt-1">
                  <MissionStatusBadge status={mission.status} />
                </dd>
              </div>
              <div>
                <dt className="font-medium">Selected model set</dt>
                <dd className="mt-1 text-muted-foreground">
                  {mission.model_set_name ?? mission.model_set_id}
                </dd>
              </div>
              <div>
                <dt className="font-medium">Associated project</dt>
                <dd className="mt-1 text-muted-foreground">{mission.project_name ?? "None"}</dd>
              </div>
              <div>
                <dt className="font-medium">Active blueprint version</dt>
                <dd className="mt-1 text-muted-foreground">
                  {mission.active_blueprint_version
                    ? `v${mission.active_blueprint_version}`
                    : "None"}
                </dd>
              </div>
              <div>
                <dt className="font-medium">Created date</dt>
                <dd className="mt-1 text-muted-foreground">
                  {new Date(mission.created_at).toLocaleString()}
                </dd>
              </div>
              <div>
                <dt className="font-medium">Updated date</dt>
                <dd className="mt-1 text-muted-foreground">
                  {new Date(mission.updated_at).toLocaleString()}
                </dd>
              </div>
            </dl>
          </GlassCard>
        )}
      </div>
    </AppShell>
  );
}
