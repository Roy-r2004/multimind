import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useCallback, useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { GlassCard, PageHeader } from "@/components/cinematic/PageChrome";
import { Modal } from "@/components/Modal";
import { MissionStatusBadge } from "@/components/scraping/MissionStatusBadge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ApiClientError } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";
import {
  getScrapingMission,
  listScrapingBlueprints,
  listScrapingExecutions,
  listScrapingRuns,
  planScrapingTeam,
  updateScrapingMission,
} from "@/lib/scraping/api";
import { countryLabel, SCRAPING_COUNTRIES } from "@/lib/scraping/countries";
import type {
  ScrapingBlueprint,
  ScrapingExecutionSummary,
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
  const [latestExecution, setLatestExecution] = useState<ScrapingExecutionSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [planning, setPlanning] = useState(false);
  const [showCountryModal, setShowCountryModal] = useState(false);
  const [countryCode, setCountryCode] = useState("");
  const [savingCountry, setSavingCountry] = useState(false);

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
      .then(async ([missionResult, blueprintResult, runResult]) => {
        setMission(missionResult);
        setBlueprints(blueprintResult);
        setRuns(runResult);
        const preferredRun =
          runResult.find((run) => run.blueprint_id === missionResult.active_blueprint_id) ??
          runResult[0];
        if (!preferredRun) {
          setLatestExecution(null);
          return;
        }
        const executions = await listScrapingExecutions(auth, preferredRun.id);
        setLatestExecution(executions[0] ?? null);
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

  const resultsReady =
    latestExecution &&
    ["completed", "failed", "cancelled"].includes(latestExecution.status) &&
    (latestExecution.records_verified > 0 ||
      latestExecution.documents_found > 0 ||
      latestExecution.sources_discovered > 0);
  const scrapeRunning =
    latestExecution &&
    ["queued", "running", "cancel_requested"].includes(latestExecution.status);

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

  async function handleSetCountry() {
    const auth = authHeaders();
    if (!auth || !mission) {
      return;
    }
    setSavingCountry(true);
    setError(null);
    try {
      const updated = await updateScrapingMission(auth, mission.id, { country_code: countryCode });
      setMission(updated);
      setShowCountryModal(false);
      setCountryCode("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to set country");
    } finally {
      setSavingCountry(false);
    }
  }

  return (
    <AppShell>
      <div className="mx-auto max-w-4xl px-6 py-10">
        <PageHeader
          eyebrow="Scraping Council"
          title={mission?.title ?? "Scraping Mission"}
          description="Your scrape job — results first, setup second."
          action={
            resultsReady && latestExecution ? (
              <Link
                to="/scraping/$missionId/executions/$executionId"
                params={{ missionId, executionId: latestExecution.id }}
                className="rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground"
              >
                View results
              </Link>
            ) : (
              <Link
                to="/scraping/$missionId/blueprint"
                params={{ missionId }}
                className="rounded-xl border border-border px-4 py-2.5 text-sm font-medium"
              >
                Blueprint
              </Link>
            )
          }
        />
        {error && <GlassCard className="mt-8 p-8 text-sm text-destructive">{error}</GlassCard>}
        {!error && !mission && (
          <GlassCard className="mt-8 p-8 text-sm text-muted-foreground">
            Loading mission...
          </GlassCard>
        )}

        {mission && resultsReady && latestExecution && (
          <GlassCard className="mt-8 border-emerald-500/40 bg-emerald-500/10 p-6">
            <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
              <div>
                <p className="text-lg font-semibold">Scrape results are ready</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  {latestExecution.records_verified} facilities ·{" "}
                  {latestExecution.documents_found} pages ·{" "}
                  {latestExecution.sources_discovered} sources found
                </p>
              </div>
              <Button
                type="button"
                size="lg"
                onClick={() =>
                  void navigate({
                    to: "/scraping/$missionId/executions/$executionId",
                    params: { missionId, executionId: latestExecution.id },
                  })
                }
              >
                Open results
              </Button>
            </div>
          </GlassCard>
        )}

        {mission && scrapeRunning && latestExecution && (
          <GlassCard className="mt-8 border-sky-500/40 bg-sky-500/10 p-6">
            <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
              <div>
                <p className="text-lg font-semibold">Scrape is running…</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  Watch live progress. Facilities appear when extraction finishes.
                </p>
              </div>
              <Button
                type="button"
                onClick={() =>
                  void navigate({
                    to: "/scraping/$missionId/executions/$executionId",
                    params: { missionId, executionId: latestExecution.id },
                  })
                }
              >
                Watch progress
              </Button>
            </div>
          </GlassCard>
        )}

        {mission && (
          <GlassCard className="mt-6 p-6">
            <div className="flex flex-wrap items-center gap-2">
              <MissionStatusBadge status={mission.status} />
              <Badge variant="outline">
                {countryLabel(mission.country_code, mission.country_name)}
              </Badge>
              {!mission.country_code && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => setShowCountryModal(true)}
                >
                  Set Country
                </Button>
              )}
            </div>
            <p className="mt-4 whitespace-pre-wrap text-sm text-muted-foreground">
              {mission.original_prompt}
            </p>
            <p className="mt-3 text-xs text-muted-foreground">
              Model set: {mission.model_set_name ?? mission.model_set_id}
              {mission.active_blueprint_version
                ? ` · Blueprint v${mission.active_blueprint_version}`
                : ""}
            </p>
          </GlassCard>
        )}

        {mission && activeApprovedBlueprint && !activeBlueprintRun && (
          <GlassCard className="mt-6 p-6">
            <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
              <div>
                <h2 className="text-base font-semibold">Next: prepare scrape</h2>
                <p className="mt-2 text-sm text-muted-foreground">
                  Create the AI team, then start the scrape to get facility results.
                </p>
              </div>
              <Button type="button" disabled={planning} onClick={() => void handlePlanTeam()}>
                {planning ? "Preparing…" : "Continue"}
              </Button>
            </div>
          </GlassCard>
        )}

        {mission &&
          activeApprovedBlueprint &&
          activeBlueprintRun &&
          !resultsReady &&
          !scrapeRunning && (
            <GlassCard className="mt-6 p-6">
              <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
                <div>
                  <h2 className="text-base font-semibold">Ready to scrape</h2>
                  <p className="mt-2 text-sm text-muted-foreground">
                    Start the scrape to search the web, extract facilities, and download Excel.
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
                  {activeBlueprintRun.status === "planning" ? "Preparing…" : "Start scrape"}
                </Button>
              </div>
            </GlassCard>
          )}

        {mission && (
          <details className="mt-6 rounded-xl border border-border bg-card/40 p-4 text-sm">
            <summary className="cursor-pointer font-medium">Advanced / setup</summary>
            <div className="mt-3 flex flex-wrap gap-2">
              <Link
                to="/scraping/$missionId/blueprint"
                params={{ missionId }}
                className="rounded-lg border border-border px-3 py-2"
              >
                Blueprint
              </Link>
              <Link
                to="/scraping/$missionId/runs"
                params={{ missionId }}
                className="rounded-lg border border-border px-3 py-2"
              >
                All runs
              </Link>
            </div>
          </details>
        )}
      </div>
      <Modal
        open={showCountryModal}
        onClose={savingCountry ? () => undefined : () => setShowCountryModal(false)}
        title="Set Mission Country"
        size="md"
      >
        <div className="space-y-4">
          <p className="text-sm text-muted-foreground">
            Set the country for this mission. One mission = one country.
          </p>
          <input
            list="mission-country-options"
            value={countryCode}
            onChange={(event) => setCountryCode(event.target.value.toUpperCase())}
            placeholder="Search country or enter code, e.g. LB"
            className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/30"
          />
          <datalist id="mission-country-options">
            {SCRAPING_COUNTRIES.map((country) => (
              <option key={country.code} value={country.code}>
                {country.name}
              </option>
            ))}
          </datalist>
          <div className="flex justify-end gap-2">
            <Button
              type="button"
              variant="outline"
              disabled={savingCountry}
              onClick={() => setShowCountryModal(false)}
            >
              Cancel
            </Button>
            <Button
              type="button"
              disabled={savingCountry || !countryCode.trim()}
              onClick={() => void handleSetCountry()}
            >
              {savingCountry ? "Saving..." : "Set Country"}
            </Button>
          </div>
        </div>
      </Modal>
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
