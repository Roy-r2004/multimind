import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { Modal } from "@/components/Modal";
import { GlassCard, PageHeader } from "@/components/cinematic/PageChrome";
import { BlueprintApprovalBar } from "@/components/scraping/BlueprintApprovalBar";
import { BlueprintEditModal } from "@/components/scraping/BlueprintEditModal";
import { BlueprintVersionList } from "@/components/scraping/BlueprintVersionList";
import { BlueprintViewer } from "@/components/scraping/BlueprintViewer";
import { GeneratedBlueprintContent } from "@/components/scraping/GeneratedBlueprintContent";
import { MissionStatusBadge } from "@/components/scraping/MissionStatusBadge";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";
import {
  approveScrapingBlueprint,
  discardScrapingBlueprint,
  editScrapingBlueprint,
  getScrapingBlueprintStatus,
  getScrapingMission,
  listScrapingBlueprints,
  regenerateScrapingBlueprint,
  rejectScrapingBlueprint,
  requestScrapingBlueprintChanges,
} from "@/lib/scraping/api";
import {
  activeApprovedBlueprint,
  canDiscardBlueprint,
  canEditBlueprint,
  canRegenerateBlueprint,
  scrapingCtaMessage,
} from "@/lib/scraping/blueprintActions";
import {
  isBlueprintPollingStatus,
  pollBlueprintUntilSettled,
} from "@/lib/scraping/blueprintPolling";
import { countryLabel } from "@/lib/scraping/countries";
import type { ScrapingBlueprint, ScrapingMissionDetail } from "@/lib/scraping/types";

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
  const [selectedId, setSelectedId] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [confirmAction, setConfirmAction] = useState<"regenerate" | "discard" | null>(null);
  const [phaseTwoMessage, setPhaseTwoMessage] = useState<string | null>(null);

  const selected = useMemo(
    () => blueprints.find((blueprint) => blueprint.id === selectedId) ?? blueprints[0] ?? null,
    [blueprints, selectedId],
  );
  const selectedBlueprintId = selected?.id;
  const selectedBlueprintStatus = selected?.status;

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
        const [missionResult, blueprintResult] = await Promise.all([
          getScrapingMission(auth, missionId),
          listScrapingBlueprints(auth, missionId),
        ]);
        setMission(missionResult);
        setBlueprints(blueprintResult);
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

  useEffect(() => {
    if (
      !selectedBlueprintId ||
      !selectedBlueprintStatus ||
      !isBlueprintPollingStatus(selectedBlueprintStatus)
    ) {
      return;
    }
    const auth = authHeaders();
    if (!auth) {
      void navigate({ to: "/login" });
      return;
    }
    const controller = new AbortController();
    void pollBlueprintUntilSettled(
      (signal) => getScrapingBlueprintStatus(auth, missionId, selectedBlueprintId, signal),
      (blueprint) =>
        setBlueprints((current) =>
          current.map((item) => (item.id === blueprint.id ? blueprint : item)),
        ),
      { signal: controller.signal },
    ).catch((err: unknown) =>
      setError(err instanceof Error ? err.message : "Failed to refresh blueprint status"),
    );
    return () => controller.abort();
  }, [authHeaders, missionId, navigate, selectedBlueprintId, selectedBlueprintStatus]);

  const activeApproved = useMemo(
    () => activeApprovedBlueprint(blueprints, mission?.active_blueprint_id),
    [blueprints, mission],
  );

  async function act(
    action: (auth: { token: string; orgId: string }) => Promise<ScrapingBlueprint>,
    preferredId?: string,
  ) {
    const auth = authHeaders();
    if (!auth) {
      void navigate({ to: "/login" });
      return;
    }
    setActionBusy(true);
    setError(null);
    try {
      const blueprint = await action(auth);
      await load(preferredId ?? blueprint.id);
      window.dispatchEvent(new CustomEvent("scraping-missions-updated"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Blueprint action failed.");
    } finally {
      setActionBusy(false);
      setConfirmAction(null);
    }
  }

  return (
    <AppShell>
      <div className="mx-auto max-w-5xl px-6 py-10">
        <PageHeader
          eyebrow="Scraping Council"
          title={mission?.title ?? "Blueprint"}
          description={
            mission
              ? `Review the generated blueprint before approval. ${countryLabel(
                  mission.country_code,
                  mission.country_name,
                )}.`
              : "Review the generated blueprint before approval."
          }
        />
        {loading && (
          <GlassCard className="mt-8 p-8 text-sm text-muted-foreground">
            Loading blueprint...
          </GlassCard>
        )}
        {error && <GlassCard className="mt-8 p-8 text-sm text-destructive">{error}</GlassCard>}
        {!loading && !error && !selected && (
          <GlassCard className="mt-8 p-12 text-center text-sm text-muted-foreground">
            No blueprint versions yet.
          </GlassCard>
        )}
        {selected && (
          <div className="mt-8 space-y-5">
            {mission && (
              <GlassCard className="p-5">
                <h2 className="mb-3 text-sm font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                  Version History
                </h2>
                <BlueprintVersionList
                  blueprints={blueprints}
                  mission={mission}
                  selectedId={selected.id}
                  onSelect={setSelectedId}
                />
              </GlassCard>
            )}
            <GlassCard className="p-5">
              <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
                <div className="space-y-2 text-sm">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{blueprintDisplayName(selected)}</span>
                    <span className="text-xs text-muted-foreground">
                      Version {selected.version}
                    </span>
                    <MissionStatusBadge status={selected.status} />
                  </div>
                  <div className="text-muted-foreground">
                    Created {new Date(selected.created_at).toLocaleString()}
                  </div>
                  {mission?.country_iso3 && (
                    <div className="text-muted-foreground">
                      Country: {mission.country_name} · {mission.country_iso3}
                      {mission.continent ? ` · ${mission.continent}` : ""}
                    </div>
                  )}
                  {selected.queued_at && (
                    <div className="text-muted-foreground">
                      Queued {new Date(selected.queued_at).toLocaleString()}
                    </div>
                  )}
                  {selected.started_at && (
                    <div className="text-muted-foreground">
                      Started {new Date(selected.started_at).toLocaleString()}
                    </div>
                  )}
                  {selected.completed_at && (
                    <div className="text-muted-foreground">
                      Completed {new Date(selected.completed_at).toLocaleString()}
                    </div>
                  )}
                  {selected.provider && (
                    <div className="text-muted-foreground">
                      Provider: {selected.provider}
                      {selected.provider_model_id ? ` · ${selected.provider_model_id}` : ""}
                    </div>
                  )}
                  {selected.approved_at && (
                    <div className="text-muted-foreground">
                      Approved {new Date(selected.approved_at).toLocaleString()}
                    </div>
                  )}
                  {selected.rejected_at && (
                    <div className="text-muted-foreground">
                      Rejected {new Date(selected.rejected_at).toLocaleString()}
                    </div>
                  )}
                  {selected.discarded_at && (
                    <div className="text-muted-foreground">
                      Discarded {new Date(selected.discarded_at).toLocaleString()}
                    </div>
                  )}
                  {selected.revision_request && (
                    <div className="text-muted-foreground">
                      Revision instruction: {selected.revision_request}
                    </div>
                  )}
                </div>
                <Link
                  to="/scraping/$missionId"
                  params={{ missionId }}
                  className="text-sm text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
                >
                  Back to mission
                </Link>
              </div>
            </GlassCard>
            {isBlueprintPollingStatus(selected.status) && (
              <GlassCard className="p-6 text-sm text-muted-foreground">
                <div className="font-medium text-foreground">
                  {selected.status === "queued"
                    ? "Blueprint generation is queued."
                    : "Blueprint generation is running."}
                </div>
                <p className="mt-2">
                  This page refreshes automatically while generation is active.
                </p>
              </GlassCard>
            )}
            {selected.status === "failed" && (
              <GlassCard className="p-6 text-sm">
                <div className="font-medium">Blueprint generation failed.</div>
                <p className="mt-2 text-muted-foreground">
                  {selected.generation_error ||
                    selected.error_message ||
                    "No additional details are available."}
                </p>
              </GlassCard>
            )}
            {selected.human_readable_blueprint || selected.structured_blueprint ? (
              <GeneratedBlueprintContent
                humanReadable={selected.human_readable_blueprint}
                structured={selected.structured_blueprint}
                citations={selected.citations}
              />
            ) : selected.blueprint_json ? (
              <BlueprintViewer content={selected.blueprint_json} />
            ) : !isBlueprintPollingStatus(selected.status) && selected.status !== "failed" ? (
              <GlassCard className="p-8 text-sm text-muted-foreground">
                Blueprint content is not available.
              </GlassCard>
            ) : null}
            <div className="flex flex-wrap gap-2">
              {canEditBlueprint(selected.status) && (
                <Button type="button" variant="outline" onClick={() => setEditOpen(true)}>
                  Edit Blueprint
                </Button>
              )}
              {canRegenerateBlueprint(selected.status) && (
                <Button
                  type="button"
                  variant="outline"
                  disabled={actionBusy}
                  onClick={() => setConfirmAction("regenerate")}
                >
                  Regenerate
                </Button>
              )}
              {canDiscardBlueprint(selected.status) && (
                <Button
                  type="button"
                  variant="destructive"
                  disabled={actionBusy}
                  onClick={() => setConfirmAction("discard")}
                >
                  Discard
                </Button>
              )}
            </div>
            <BlueprintApprovalBar
              blueprint={selected}
              activeBlueprintId={mission?.active_blueprint_id}
              onApprove={() =>
                act((auth) => approveScrapingBlueprint(auth, selected.id), selected.id)
              }
              onReject={(reason) =>
                act((auth) => rejectScrapingBlueprint(auth, selected.id, reason), selected.id)
              }
              onRequestChanges={(instructions) =>
                act(
                  (auth) => requestScrapingBlueprintChanges(auth, selected.id, instructions),
                  undefined,
                )
              }
              onGenerateNewVersion={() =>
                act((auth) => regenerateScrapingBlueprint(auth, selected.id), undefined)
              }
            />
            <GlassCard className="p-5">
              <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
                <div>
                  <h2 className="text-base font-semibold">Maximum-Coverage Scraping</h2>
                  <p className="mt-2 text-sm text-muted-foreground">
                    {activeApproved
                      ? scrapingCtaMessage(activeApproved)
                      : "Approve a blueprint before campaign execution becomes available."}
                  </p>
                </div>
                <Button
                  type="button"
                  disabled={!activeApproved}
                  onClick={() =>
                    activeApproved &&
                    setPhaseTwoMessage("Campaign execution will be added in Phase 2.")
                  }
                >
                  Start Maximum-Coverage Scraping
                </Button>
              </div>
            </GlassCard>
            {phaseTwoMessage && (
              <GlassCard className="p-4 text-sm text-muted-foreground">{phaseTwoMessage}</GlassCard>
            )}
            <BlueprintEditModal
              blueprint={selected}
              open={editOpen}
              busy={actionBusy}
              onClose={() => setEditOpen(false)}
              onSave={async (humanReadable, structured) => {
                await act(
                  (auth) =>
                    editScrapingBlueprint(auth, selected.id, {
                      human_readable_blueprint: humanReadable,
                      structured_blueprint: structured,
                    }),
                  selected.id,
                );
                setEditOpen(false);
              }}
            />
            <Modal
              open={confirmAction !== null}
              onClose={actionBusy ? () => undefined : () => setConfirmAction(null)}
              title={confirmAction === "discard" ? "Discard Blueprint" : "Regenerate Blueprint"}
            >
              <div className="space-y-4 text-sm">
                <p>
                  {confirmAction === "discard"
                    ? "This version will remain in audit history and cannot become the active approved blueprint."
                    : "A new blueprint version will be generated. The current version will be preserved."}
                </p>
                <div className="flex justify-end gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    disabled={actionBusy}
                    onClick={() => setConfirmAction(null)}
                  >
                    Cancel
                  </Button>
                  <Button
                    type="button"
                    variant={confirmAction === "discard" ? "destructive" : "default"}
                    disabled={actionBusy}
                    onClick={() =>
                      confirmAction === "discard"
                        ? void act(
                            (auth) => discardScrapingBlueprint(auth, selected.id),
                            selected.id,
                          )
                        : void act((auth) => regenerateScrapingBlueprint(auth, selected.id))
                    }
                  >
                    Confirm
                  </Button>
                </div>
              </div>
            </Modal>
          </div>
        )}
      </div>
    </AppShell>
  );
}
