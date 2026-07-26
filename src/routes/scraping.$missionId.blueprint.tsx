import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { Modal } from "@/components/Modal";
import { GlassCard, PageHeader } from "@/components/cinematic/PageChrome";
import { BlueprintApprovalBar } from "@/components/scraping/BlueprintApprovalBar";
import { BlueprintEditModal } from "@/components/scraping/BlueprintEditModal";
import { BlueprintVersionList } from "@/components/scraping/BlueprintVersionList";
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
  startMissionCampaign,
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
  mergeBlueprintPollState,
  pollBlueprintUntilSettled,
  resolveFollowedBlueprintSelection,
} from "@/lib/scraping/blueprintPolling";
import { countryLabel } from "@/lib/scraping/countries";
import type { ScrapingBlueprint, ScrapingMissionDetail } from "@/lib/scraping/types";

function blueprintDisplayName(blueprint: ScrapingBlueprint): string {
  return blueprint.display_name?.trim() || `Blueprint v${blueprint.version}`;
}

export const Route = createFileRoute("/scraping/$missionId/blueprint")({
  head: () => ({ meta: [{ title: "Country Blueprint - MultiAI" }] }),
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
  const mountedRef = useRef(true);
  const pollSequenceRef = useRef(0);
  const followBlueprintIdRef = useRef<string | null>(null);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const selected = useMemo(
    () => blueprints.find((blueprint) => blueprint.id === selectedId) ?? blueprints[0] ?? null,
    [blueprints, selectedId],
  );
  const selectedBlueprintId = selected?.id;
  const shouldPollSelected = Boolean(selected && isBlueprintPollingStatus(selected.status));

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
        if (!mountedRef.current) return blueprintResult;
        setMission(missionResult);
        setBlueprints(blueprintResult);
        setSelectedId((currentId) =>
          resolveFollowedBlueprintSelection(
            blueprintResult,
            preferredBlueprintId ?? currentId,
            preferredBlueprintId ?? followBlueprintIdRef.current,
          ),
        );
        return blueprintResult;
      } catch (err) {
        if (mountedRef.current) {
          setError(err instanceof Error ? err.message : "Failed to load blueprint");
        }
        return [];
      } finally {
        if (mountedRef.current) setLoading(false);
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
    if (!selectedBlueprintId || !shouldPollSelected) {
      return;
    }
    const auth = authHeaders();
    if (!auth) {
      void navigate({ to: "/login" });
      return;
    }
    const controller = new AbortController();
    const watchedId = selectedBlueprintId;

    void pollBlueprintUntilSettled(
      async (signal) => {
        const sequence = ++pollSequenceRef.current;
        const [polled, listed] = await Promise.all([
          getScrapingBlueprintStatus(auth, missionId, watchedId, signal),
          listScrapingBlueprints(auth, missionId, signal),
        ]);
        if (signal.aborted || !mountedRef.current || sequence !== pollSequenceRef.current) {
          return polled;
        }
        setBlueprints((current) => mergeBlueprintPollState(current, listed, polled));
        setSelectedId((currentId) =>
          resolveFollowedBlueprintSelection(listed, currentId, followBlueprintIdRef.current),
        );
        const followedId = followBlueprintIdRef.current;
        if (followedId) {
          const followed = listed.find((blueprint) => blueprint.id === followedId);
          if (followed && !isBlueprintPollingStatus(followed.status)) {
            followBlueprintIdRef.current = null;
          }
        }
        return polled;
      },
      () => {
        // State is applied inside fetchStatus so list + selected detail stay in sync.
      },
      { signal: controller.signal },
    ).catch((err: unknown) => {
      if (!mountedRef.current || controller.signal.aborted) return;
      setError(err instanceof Error ? err.message : "Failed to refresh blueprint status");
    });

    return () => {
      pollSequenceRef.current += 1;
      controller.abort();
    };
  }, [authHeaders, missionId, navigate, selectedBlueprintId, shouldPollSelected]);

  const activeApproved = useMemo(
    () => activeApprovedBlueprint(blueprints, mission?.active_blueprint_id),
    [blueprints, mission],
  );

  async function act(
    action: (auth: { token: string; orgId: string }) => Promise<ScrapingBlueprint>,
    options?: { preferredId?: string; follow?: boolean },
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
      if (options?.follow) {
        followBlueprintIdRef.current = blueprint.id;
      }
      await load(options?.preferredId ?? blueprint.id);
      window.dispatchEvent(new CustomEvent("scraping-missions-updated"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Blueprint action failed.");
    } finally {
      setActionBusy(false);
      setConfirmAction(null);
    }
  }

  async function startCampaign() {
    const auth = authHeaders();
    if (!auth || !activeApproved || actionBusy) {
      if (!auth) void navigate({ to: "/login" });
      return;
    }
    setActionBusy(true);
    setError(null);
    try {
      const campaign = await startMissionCampaign(auth, missionId);
      void navigate({
        to: "/scraping/$missionId/campaigns/$executionId",
        params: { missionId, executionId: campaign.id },
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start the campaign.");
    } finally {
      setActionBusy(false);
    }
  }

  function selectVersion(blueprintId: string) {
    followBlueprintIdRef.current = null;
    setSelectedId(blueprintId);
  }

  const showReviewContent =
    selected &&
    !isBlueprintPollingStatus(selected.status) &&
    selected.status !== "failed" &&
    Boolean(selected.structured_blueprint || selected.human_readable_blueprint);

  return (
    <AppShell>
      <div className="mx-auto max-w-5xl px-6 py-10">
        <PageHeader
          eyebrow="Scraping Mission"
          title={mission?.title ?? "Country Blueprint"}
          description={
            mission
              ? `Review the Country Blueprint before approval. ${countryLabel(
                  mission.country_code,
                  mission.country_name,
                )}.`
              : "Review the Country Blueprint before approval."
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
                  onSelect={selectVersion}
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
                  {mission?.country_name && (
                    <div className="text-muted-foreground">
                      Country: {mission.country_name}
                      {mission.country_code ? ` · ${mission.country_code}` : ""}
                      {mission.country_iso3 ? ` · ${mission.country_iso3}` : ""}
                      {mission.continent ? ` · ${mission.continent}` : ""}
                    </div>
                  )}
                  {selected.completed_at && (
                    <div className="text-muted-foreground">
                      Completed {new Date(selected.completed_at).toLocaleString()}
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

            <div className="flex flex-wrap gap-2">
              {canEditBlueprint(selected.status) && (
                <Button type="button" variant="outline" onClick={() => setEditOpen(true)}>
                  Edit
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

            {isBlueprintPollingStatus(selected.status) && (
              <GlassCard className="p-6 text-sm text-muted-foreground">
                <div className="font-medium text-foreground">
                  {selected.status === "queued"
                    ? "Country Blueprint generation is queued."
                    : "Country Blueprint generation is running."}
                </div>
                <p className="mt-2">
                  Research Pipeline progress refreshes automatically. Completed review content
                  appears here when status becomes ready for review.
                </p>
              </GlassCard>
            )}
            {selected.status === "failed" && (
              <GlassCard className="p-6 text-sm">
                <div className="font-medium">Country Blueprint generation failed.</div>
                <p className="mt-2 text-muted-foreground">
                  {selected.generation_error ||
                    selected.error_message ||
                    "No additional details are available."}
                </p>
              </GlassCard>
            )}
            {showReviewContent && (
              <GeneratedBlueprintContent
                structured={selected.structured_blueprint}
                citations={selected.citations}
                countryIso2={mission?.country_code}
              />
            )}
            {!showReviewContent &&
              !isBlueprintPollingStatus(selected.status) &&
              selected.status !== "failed" && (
                <GlassCard className="p-8 text-sm text-muted-foreground">
                  Blueprint content is not available.
                </GlassCard>
              )}

            <BlueprintApprovalBar
              blueprint={selected}
              activeBlueprintId={mission?.active_blueprint_id}
              onApprove={() =>
                act((auth) => approveScrapingBlueprint(auth, selected.id), {
                  preferredId: selected.id,
                })
              }
              onReject={(reason) =>
                act((auth) => rejectScrapingBlueprint(auth, selected.id, reason), {
                  preferredId: selected.id,
                })
              }
              onRequestChanges={(instructions) =>
                act((auth) => requestScrapingBlueprintChanges(auth, selected.id, instructions), {
                  follow: true,
                })
              }
              onGenerateNewVersion={() =>
                act((auth) => regenerateScrapingBlueprint(auth, selected.id), { follow: true })
              }
            />
            <GlassCard className="p-5">
              <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
                <div>
                  <h2 className="text-base font-semibold">Maximum-Coverage Scraping</h2>
                  <p className="mt-2 text-sm text-muted-foreground">
                    {activeApproved
                      ? scrapingCtaMessage(activeApproved)
                      : "Approve a Country Blueprint before campaign execution becomes available."}
                  </p>
                </div>
                <Button
                  type="button"
                  disabled={!activeApproved || actionBusy}
                  onClick={() => void startCampaign()}
                >
                  {actionBusy ? "Starting…" : "Start Maximum-Coverage Scraping"}
                </Button>
              </div>
            </GlassCard>
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
                  { preferredId: selected.id },
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
                    : "A new Country Blueprint version will be generated. The current version will be preserved."}
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
                        ? void act((auth) => discardScrapingBlueprint(auth, selected.id), {
                            preferredId: selected.id,
                          })
                        : void act((auth) => regenerateScrapingBlueprint(auth, selected.id), {
                            follow: true,
                          })
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
