import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useCallback, useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { GlassCard, PageHeader } from "@/components/cinematic/PageChrome";
import { MissionStatusBadge } from "@/components/scraping/MissionStatusBadge";
import { Button } from "@/components/ui/button";
import { ApiClientError } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";
import {
  getScrapingMission,
  listScrapingBlueprints,
  listScrapingRuns,
  planScrapingTeam,
} from "@/lib/scraping/api";
import type {
  ScrapingBlueprint,
  ScrapingMissionDetail,
  ScrapingRunConflictDetails,
  ScrapingRunSummary,
} from "@/lib/scraping/types";

export const Route = createFileRoute("/scraping/$missionId/")({
  head: () => ({ meta: [{ title: "Scraping Mission - MultiAI" }] }),
  component: ScrapingMissionPage,
});

function ScrapingMissionPage() {
  const { missionId } = Route.useParams();
  const { authHeaders } = useAuth();
  const navigate = useNavigate();
  const [mission, setMission] = useState<ScrapingMissionDetail | null>(null);
  const [blueprints, setBlueprints] = useState<ScrapingBlueprint[]>([]);
  const [runs, setRuns] = useState<ScrapingRunSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [planning, setPlanning] = useState(false);

  const loadMission = useCallback(() => {
    const auth = authHeaders();
    if (!auth) {
      void navigate({ to: "/login" });
      return;
    }
    setError(null);
    void Promise.all([
      getScrapingMission(auth, missionId),
      listScrapingBlueprints(auth, missionId),
      listScrapingRuns(auth, missionId),
    ])
      .then(([missionResult, blueprintResult, runResult]) => {
        setMission(missionResult);
        setBlueprints(blueprintResult);
        setRuns(runResult);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load mission"));
  }, [authHeaders, missionId, navigate]);

  useEffect(() => {
    loadMission();
  }, [loadMission]);

  useEffect(() => {
    window.addEventListener("scraping-missions-updated", loadMission);
    return () => window.removeEventListener("scraping-missions-updated", loadMission);
  }, [loadMission]);

  const activeApprovedBlueprint = mission
    ? blueprints.find(
        (blueprint) =>
          blueprint.id === mission.active_blueprint_id && blueprint.status === "approved",
      )
    : null;
  const activeBlueprintRun = activeApprovedBlueprint
    ? runs.find((run) => run.blueprint_id === activeApprovedBlueprint.id)
    : null;

  async function handlePlanTeam() {
    const auth = authHeaders();
    if (!auth) {
      void navigate({ to: "/login" });
      return;
    }
    setPlanning(true);
    setError(null);
    try {
      const run = await planScrapingTeam(auth, missionId);
      void navigate({
        to: "/scraping/$missionId/runs/$runId",
        params: { missionId, runId: run.id },
      });
    } catch (err) {
      const existingRun = existingRunConflictDetails(err);
      if (existingRun) {
        void navigate({
          to: "/scraping/$missionId/runs/$runId",
          params: { missionId, runId: existingRun.existing_run_id },
        });
        return;
      }
      setError(err instanceof Error ? err.message : "Failed to plan AI scraping team");
    } finally {
      setPlanning(false);
    }
  }

  return (
    <AppShell>
      <div className="mx-auto max-w-4xl px-6 py-10">
        <PageHeader
          eyebrow="Scraping Council"
          title={mission?.title ?? "Scraping Mission"}
          description="Mission overview and blueprint status."
          action={
            <div className="flex flex-wrap gap-2">
              <Link
                to="/scraping/$missionId/runs"
                params={{ missionId }}
                className="rounded-xl border border-border px-4 py-2.5 text-sm font-medium"
              >
                Runs
              </Link>
              <Link
                to="/scraping/$missionId/blueprint"
                params={{ missionId }}
                className="rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground"
              >
                Open Blueprint
              </Link>
            </div>
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
        {mission && activeApprovedBlueprint && !activeBlueprintRun && (
          <GlassCard className="mt-6 p-6">
            <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
              <div>
                <h2 className="text-base font-semibold">Plan AI Scraping Team</h2>
                <p className="mt-2 text-sm text-muted-foreground">
                  The orchestrator will analyze the approved blueprint and choose the required AI
                  scraping agents. No websites will be scraped yet.
                </p>
              </div>
              <Button type="button" disabled={planning} onClick={() => void handlePlanTeam()}>
                {planning ? "Planning AI Team..." : "Plan AI Scraping Team"}
              </Button>
            </div>
          </GlassCard>
        )}
        {mission && activeApprovedBlueprint && activeBlueprintRun && (
          <GlassCard className="mt-6 p-6">
            <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
              <div>
                <h2 className="text-base font-semibold">View AI Team Plan</h2>
                <p className="mt-2 text-sm text-muted-foreground">
                  This approved blueprint version already has a persisted AI team plan.
                </p>
              </div>
              <Button
                type="button"
                disabled={activeBlueprintRun.status === "planning"}
                onClick={() =>
                  void navigate({
                    to: "/scraping/$missionId/runs/$runId",
                    params: { missionId, runId: activeBlueprintRun.id },
                  })
                }
              >
                {activeBlueprintRun.status === "planning"
                  ? "Planning AI Team..."
                  : "View AI Team Plan"}
              </Button>
            </div>
          </GlassCard>
        )}
      </div>
    </AppShell>
  );
}

function existingRunConflictDetails(error: unknown): ScrapingRunConflictDetails | null {
  if (!(error instanceof ApiClientError) || error.status !== 409) {
    return null;
  }
  const details = error.body?.details;
  if (
    typeof details !== "object" ||
    details === null ||
    !("existing_run_id" in details) ||
    !("existing_run_status" in details) ||
    typeof details.existing_run_id !== "string" ||
    typeof details.existing_run_status !== "string"
  ) {
    return null;
  }
  return details as ScrapingRunConflictDetails;
}
