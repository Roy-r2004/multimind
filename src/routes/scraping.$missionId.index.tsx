import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useCallback, useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { Modal } from "@/components/Modal";
import {
  DreamHeader,
  DreamPageShell,
  DreamPanel,
  dreamDetailsClass,
  dreamGhostClass,
  dreamInputClass,
  dreamMutedClass,
} from "@/components/scraping/DreamPageShell";
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

  const intensity = scrapeRunning ? "live" : "calm";

  return (
    <AppShell>
      <DreamPageShell maxWidth="max-w-4xl" intensity={intensity}>
        <DreamHeader
          eyebrow="Scraping Council · Mission hub"
          title={mission?.title ?? "Scraping Mission"}
          description="Results first. Setup stays in the drift below."
          action={
            resultsReady && latestExecution ? (
              <Link
                to="/scraping/$missionId/executions/$executionId"
                params={{ missionId, executionId: latestExecution.id }}
                className="rounded-xl council-glass-cta px-4 py-2.5 text-sm font-semibold"
              >
                Open flight
              </Link>
            ) : (
              <Link
                to="/scraping/$missionId/blueprint"
                params={{ missionId }}
                className={dreamGhostClass}
              >
                Chart
              </Link>
            )
          }
        />
        {error && <DreamPanel className="mt-8 text-sm text-rose-200">{error}</DreamPanel>}
        {!error && !mission && (
          <DreamPanel className="mt-8 text-sm text-white/60">Loading mission…</DreamPanel>
        )}

        {mission && resultsReady && latestExecution && (
          <DreamPanel tone="teal" className="dream-rise mt-8">
            <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
              <div>
                <p className="text-[11px] uppercase tracking-[0.28em] text-teal-200/80">Landed</p>
                <p className="mt-1 font-display text-2xl text-white">Flight results ready</p>
                <p className="mt-1 text-sm text-white/55">
                  {latestExecution.records_verified} facilities · {latestExecution.documents_found}{" "}
                  pages · {latestExecution.sources_discovered} sources
                </p>
              </div>
              <Button
                type="button"
                size="lg"
                className="council-glass-cta"
                onClick={() =>
                  void navigate({
                    to: "/scraping/$missionId/executions/$executionId",
                    params: { missionId, executionId: latestExecution.id },
                  })
                }
              >
                Enter dreamflight
              </Button>
            </div>
          </DreamPanel>
        )}

        {mission && scrapeRunning && latestExecution && (
          <DreamPanel tone="amber" className="dream-rise mt-8">
            <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
              <div>
                <p className="text-[11px] uppercase tracking-[0.28em] text-sky-300">In flight</p>
                <p className="mt-1 font-display text-2xl text-white">Vessel is navigating…</p>
                <p className="mt-1 text-sm text-white/55">
                  Watch stages move as sources open and facilities crystallize.
                </p>
              </div>
              <Button
                type="button"
                className="council-glass-cta"
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
          </DreamPanel>
        )}

        {mission && (
          <DreamPanel className="dream-rise dream-rise-delay-1 mt-6">
            <div className="flex flex-wrap items-center gap-2">
              <MissionStatusBadge status={mission.status} />
              <Badge
                variant="outline"
                className="border-sky-300/35 bg-sky-400/10 text-sky-100"
              >
                {countryLabel(mission.country_code, mission.country_name)}
              </Badge>
              {!mission.country_code && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="border-white/20 bg-white/5 text-white hover:bg-white/10"
                  onClick={() => setShowCountryModal(true)}
                >
                  Set Country
                </Button>
              )}
            </div>
            <p className="mt-4 whitespace-pre-wrap text-sm text-white/60">{mission.original_prompt}</p>
            <p className="mt-3 text-xs text-white/40">
              Model set: {mission.model_set_name ?? mission.model_set_id}
              {mission.active_blueprint_version
                ? ` · Chart v${mission.active_blueprint_version}`
                : ""}
            </p>
          </DreamPanel>
        )}

        {mission && activeApprovedBlueprint && !activeBlueprintRun && (
          <DreamPanel tone="amber" className="mt-6">
            <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
              <div>
                <h2 className="font-display text-lg text-white">Next: assemble the crew</h2>
                <p className="mt-2 text-sm text-white/55">
                  Chart the AI team from the approved blueprint — no websites touched yet.
                </p>
              </div>
              <Button
                type="button"
                disabled={planning}
                className="council-glass-cta"
                onClick={() => void handlePlanTeam()}
              >
                {planning ? "Preparing…" : "Continue"}
              </Button>
            </div>
          </DreamPanel>
        )}

        {mission &&
          activeApprovedBlueprint &&
          activeBlueprintRun &&
          !resultsReady &&
          !scrapeRunning && (
            <DreamPanel tone="amber" className="mt-6">
              <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
                <div>
                  <h2 className="font-display text-lg text-white">Ready for takeoff</h2>
                  <p className="mt-2 text-sm text-white/55">
                    Start the scrape — search, extract, crystallize facilities, export Excel.
                  </p>
                </div>
                <Button
                  type="button"
                  disabled={activeBlueprintRun.status === "planning"}
                  className="council-glass-cta"
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
            </DreamPanel>
          )}

        {mission && (
          <details className={`mt-6 ${dreamDetailsClass}`}>
            <summary className="cursor-pointer font-medium">Advanced / setup</summary>
            <div className="mt-3 flex flex-wrap gap-2">
              <Link
                to="/scraping/$missionId/blueprint"
                params={{ missionId }}
                className="rounded-lg border border-white/15 bg-white/5 px-3 py-2 hover:bg-white/10"
              >
                Blueprint
              </Link>
              <Link
                to="/scraping/$missionId/runs"
                params={{ missionId }}
                className="rounded-lg border border-white/15 bg-white/5 px-3 py-2 hover:bg-white/10"
              >
                All runs
              </Link>
            </div>
          </details>
        )}
      </DreamPageShell>
      <Modal
        open={showCountryModal}
        onClose={savingCountry ? () => undefined : () => setShowCountryModal(false)}
        title="Set Mission Country"
        size="md"
        tone="dream"
      >
        <div className="space-y-4">
          <p className={dreamMutedClass}>
            Set the country for this mission. One mission = one country.
          </p>
          <input
            list="mission-country-options"
            value={countryCode}
            onChange={(event) => setCountryCode(event.target.value.toUpperCase())}
            placeholder="Search country or enter code, e.g. LB"
            className={dreamInputClass}
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
              className="border-white/20 bg-white/5 text-white hover:bg-white/10"
              onClick={() => setShowCountryModal(false)}
            >
              Cancel
            </Button>
            <Button
              type="button"
              disabled={savingCountry || !countryCode.trim()}
              className="council-glass-cta"
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
