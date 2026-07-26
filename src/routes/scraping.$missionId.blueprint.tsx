import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { BlueprintApprovalBar } from "@/components/scraping/BlueprintApprovalBar";
import { BlueprintVersionList } from "@/components/scraping/BlueprintVersionList";
import { BlueprintViewer } from "@/components/scraping/BlueprintViewer";
import { DreamHeader, DreamPageShell, DreamPanel } from "@/components/scraping/DreamPageShell";
import { MissionStatusBadge } from "@/components/scraping/MissionStatusBadge";
import { Button } from "@/components/ui/button";
import { ApiClientError } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";
import {
  approveScrapingBlueprint,
  generateScrapingBlueprint,
  getScrapingMission,
  listScrapingBlueprints,
  listScrapingRuns,
  planScrapingTeam,
  rejectScrapingBlueprint,
  requestScrapingBlueprintChanges,
} from "@/lib/scraping/api";
import { countryLabel } from "@/lib/scraping/countries";
import type {
  ScrapingBlueprint,
  ScrapingMissionDetail,
  ScrapingRunConflictDetails,
  ScrapingRunSummary,
} from "@/lib/scraping/types";

function blueprintDisplayName(blueprint: ScrapingBlueprint): string {
  return blueprint.display_name?.trim() || `Blueprint v${blueprint.version}`;
}

export const Route = createFileRoute("/scraping/$missionId/blueprint")({
  head: () => ({ meta: [{ title: "Scraping Blueprint - MultiAI" }] }),
  component: ScrapingBlueprintPage,
});

function ScrapingBlueprintPage() {
  const { missionId } = Route.useParams();
  const { authHeaders } = useAuth();
  const navigate = useNavigate();
  const [mission, setMission] = useState<ScrapingMissionDetail | null>(null);
  const [blueprints, setBlueprints] = useState<ScrapingBlueprint[]>([]);
  const [runs, setRuns] = useState<ScrapingRunSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [planning, setPlanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const selected = useMemo(
    () => blueprints.find((blueprint) => blueprint.id === selectedId) ?? blueprints[0] ?? null,
    [blueprints, selectedId],
  );

  const load = useCallback(
    async (preferredBlueprintId?: string) => {
      const auth = authHeaders();
      if (!auth) {
        void navigate({ to: "/login" });
        return [];
      }
      setLoading(true);
      setError(null);
      try {
        const [missionResult, blueprintResult, runResult] = await Promise.all([
          getScrapingMission(auth, missionId),
          listScrapingBlueprints(auth, missionId),
          listScrapingRuns(auth, missionId),
        ]);
        setMission(missionResult);
        setBlueprints(blueprintResult);
        setRuns(runResult);
        setSelectedId((currentId) => {
          const preferredId = preferredBlueprintId ?? currentId;
          if (preferredId && blueprintResult.some((blueprint) => blueprint.id === preferredId)) {
            return preferredId;
          }
          return blueprintResult[0]?.id ?? "";
        });
        return blueprintResult;
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load blueprint");
        return [];
      } finally {
        setLoading(false);
      }
    },
    [authHeaders, missionId, navigate],
  );

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    function reloadMission() {
      void load(selectedId);
    }
    window.addEventListener("scraping-missions-updated", reloadMission);
    return () => window.removeEventListener("scraping-missions-updated", reloadMission);
  }, [load, selectedId]);

  async function withAuth<T>(action: (auth: { token: string; orgId: string }) => Promise<T>) {
    const auth = authHeaders();
    if (!auth) {
      void navigate({ to: "/login" });
      throw new Error("Authentication required");
    }
    setError(null);
    setSuccess(null);
    try {
      return await action(auth);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Action failed");
      throw err;
    }
  }

  async function handlePlanTeam() {
    const auth = authHeaders();
    if (!auth) {
      void navigate({ to: "/login" });
      return;
    }
    setError(null);
    setSuccess(null);
    setPlanning(true);
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

  const selectedRun = selected ? runs.find((run) => run.blueprint_id === selected.id) : null;

  return (
    <AppShell>
      <DreamPageShell>
        <DreamHeader
          eyebrow="Scraping Council · Flight chart"
          title={mission?.title ?? "Blueprint"}
          description={
            mission
              ? `Review the chart before takeoff. ${countryLabel(
                  mission.country_code,
                  mission.country_name,
                )}.`
              : "Review the chart before takeoff."
          }
        />
        {loading && (
          <DreamPanel className="mt-8 text-sm text-muted-foreground">Loading chart…</DreamPanel>
        )}
        {error && <DreamPanel className="mt-8 text-sm text-rose-600">{error}</DreamPanel>}
        {success && !error && (
          <DreamPanel tone="teal" className="mt-8 text-sm text-teal-800">
            {success}
          </DreamPanel>
        )}
        {!loading && !error && !selected && (
          <DreamPanel className="mt-8 p-12 text-center text-sm text-muted-foreground">
            No blueprint versions yet.
          </DreamPanel>
        )}
        {selected && (
          <div className="mt-8 space-y-5">
            <DreamPanel>
              <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
                <div className="space-y-2 text-sm">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-display text-lg text-foreground">
                      {blueprintDisplayName(selected)}
                    </span>
                    <span className="text-xs text-white/45">Version {selected.version}</span>
                    <MissionStatusBadge status={selected.status} />
                  </div>
                  <div className="text-white/45">
                    Created {new Date(selected.created_at).toLocaleString()}
                  </div>
                  {selected.approved_at && (
                    <div className="text-white/45">
                      Approved {new Date(selected.approved_at).toLocaleString()}
                    </div>
                  )}
                  {selected.rejected_at && (
                    <div className="text-white/45">
                      Rejected {new Date(selected.rejected_at).toLocaleString()}
                    </div>
                  )}
                  {selected.rejection_reason && (
                    <div className="text-rose-600">Reason: {selected.rejection_reason}</div>
                  )}
                </div>
                {mission && (
                  <BlueprintVersionList
                    blueprints={blueprints}
                    mission={mission}
                    selectedId={selected.id}
                    onSelect={setSelectedId}
                  />
                )}
              </div>
            </DreamPanel>
            {selected.blueprint_json ? (
              <BlueprintViewer content={selected.blueprint_json} />
            ) : (
              <DreamPanel className="text-sm text-muted-foreground">
                Blueprint content is not available.
              </DreamPanel>
            )}
            {mission?.active_blueprint_id === selected.id &&
              selected.status === "approved" &&
              !selectedRun && (
                <DreamPanel tone="amber">
                  <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
                    <div>
                      <h2 className="font-display text-lg text-foreground">Assemble AI crew</h2>
                      <p className="mt-2 text-sm text-muted-foreground">
                        The orchestrator reads the approved chart and chooses agents. No websites
                        scraped yet.
                      </p>
                    </div>
                    <Button
                      type="button"
                      disabled={planning}
                      className="bg-primary text-primary-foreground hover:bg-primary/90"
                      onClick={() => void handlePlanTeam()}
                    >
                      {planning ? "Planning AI Team..." : "Plan AI Scraping Team"}
                    </Button>
                  </div>
                </DreamPanel>
              )}
            {mission?.active_blueprint_id === selected.id &&
              selected.status === "approved" &&
              selectedRun && (
                <DreamPanel tone="teal">
                  <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
                    <div>
                      <h2 className="font-display text-lg text-foreground">Crew already charted</h2>
                      <p className="mt-2 text-sm text-muted-foreground">
                        This approved chart already has a persisted AI team plan.
                      </p>
                    </div>
                    <Button
                      type="button"
                      disabled={selectedRun.status === "planning"}
                      className="bg-primary text-primary-foreground hover:bg-primary/90"
                      onClick={() =>
                        void navigate({
                          to: "/scraping/$missionId/runs/$runId",
                          params: { missionId, runId: selectedRun.id },
                        })
                      }
                    >
                      {selectedRun.status === "planning"
                        ? "Planning AI Team..."
                        : "View AI Team Plan"}
                    </Button>
                  </div>
                </DreamPanel>
              )}
            <BlueprintApprovalBar
              blueprint={selected}
              activeBlueprintId={mission?.active_blueprint_id}
              onApprove={() =>
                withAuth(async (auth) => {
                  const updated = await approveScrapingBlueprint(auth, selected.id);
                  await load(updated.id);
                  setSuccess(
                    `Blueprint version ${updated.version} was approved and is now active.`,
                  );
                })
              }
              onReject={(reason) =>
                withAuth(async (auth) => {
                  const updated = await rejectScrapingBlueprint(auth, selected.id, reason);
                  await load(updated.id);
                  setSuccess(`Blueprint version ${updated.version} was rejected.`);
                })
              }
              onRequestChanges={(instructions) =>
                withAuth(async (auth) => {
                  const created = await requestScrapingBlueprintChanges(
                    auth,
                    selected.id,
                    instructions,
                  );
                  await load(created.id);
                  setSuccess(
                    `Blueprint version ${created.version} was created and is awaiting approval.`,
                  );
                })
              }
              onGenerateNewVersion={() =>
                withAuth(async (auth) => {
                  const created = await generateScrapingBlueprint(auth, missionId);
                  await load(created.id);
                  setSuccess(
                    `Blueprint version ${created.version} was created and is awaiting approval.`,
                  );
                })
              }
            />
          </div>
        )}
      </DreamPageShell>
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
