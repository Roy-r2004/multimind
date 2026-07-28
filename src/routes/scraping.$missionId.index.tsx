import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { GlassCard, PageHeader } from "@/components/cinematic/PageChrome";
import { MissionStatusBadge } from "@/components/scraping/MissionStatusBadge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ApiClientError } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";
import {
  getScrapingMission,
  listMissionCampaigns,
  listScrapingBlueprints,
  startMissionCampaign,
} from "@/lib/scraping/api";
import { campaignStatusLabel, isCampaignPollingStatus } from "@/lib/scraping/campaignCockpit";
import { countryLabel } from "@/lib/scraping/countries";
import {
  isMissionCampaignActive,
  pickLatestMissionCampaign,
} from "@/lib/scraping/missionHub";
import type {
  ScrapingBlueprint,
  ScrapingExecutionConflictDetails,
  ScrapingExecutionSummary,
  ScrapingMissionDetail,
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
  const [campaigns, setCampaigns] = useState<ScrapingExecutionSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);

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
      listMissionCampaigns(auth, missionId),
    ])
      .then(([missionResult, blueprintResult, campaignResult]) => {
        setMission(missionResult);
        setBlueprints(blueprintResult);
        setCampaigns(campaignResult);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load mission"))
      .finally(() => setLoading(false));
  }, [authHeaders, missionId, navigate]);

  useEffect(() => {
    setLoading(true);
    loadMission();
  }, [loadMission]);

  useEffect(() => {
    window.addEventListener("scraping-missions-updated", loadMission);
    return () => window.removeEventListener("scraping-missions-updated", loadMission);
  }, [loadMission]);

  const activeApprovedBlueprint = useMemo(() => {
    if (!mission?.active_blueprint_id) return null;
    return (
      blueprints.find(
        (blueprint) =>
          blueprint.id === mission.active_blueprint_id && blueprint.status === "approved",
      ) ?? null
    );
  }, [blueprints, mission]);

  const latestBlueprint = blueprints[0] ?? null;
  const latestCampaign = useMemo(() => pickLatestMissionCampaign(campaigns), [campaigns]);
  const activeCampaign = useMemo(
    () => campaigns.find((campaign) => isMissionCampaignActive(campaign.status)) ?? null,
    [campaigns],
  );
  const featuredCampaign = activeCampaign ?? latestCampaign;
  const canStartScrape = Boolean(activeApprovedBlueprint) && !activeCampaign;

  async function handleStartScrape() {
    const auth = authHeaders();
    if (!auth || starting || !canStartScrape) {
      if (!auth) void navigate({ to: "/login" });
      return;
    }
    setStarting(true);
    setError(null);
    try {
      const campaign = await startMissionCampaign(auth, missionId);
      void navigate({
        to: "/scraping/$missionId/campaigns/$executionId",
        params: { missionId, executionId: campaign.id },
      });
    } catch (err) {
      const existing = existingCampaignConflictDetails(err);
      if (existing) {
        void navigate({
          to: "/scraping/$missionId/campaigns/$executionId",
          params: { missionId, executionId: existing.existing_execution_id },
        });
        return;
      }
      setError(err instanceof Error ? err.message : "Failed to start the campaign.");
    } finally {
      setStarting(false);
    }
  }

  return (
    <AppShell>
      <div className="mx-auto max-w-4xl px-6 py-10">
        <PageHeader
          eyebrow="Scraping Mission"
          title={mission?.title ?? "Scraping Mission"}
          description={
            mission
              ? `${countryLabel(mission.country_code, mission.country_name)} · Blueprint-driven research campaign`
              : "Blueprint-driven research campaign"
          }
          action={
            <Link
              to="/scraping/$missionId/blueprint"
              params={{ missionId }}
              className="rounded-xl border border-border px-4 py-2.5 text-sm font-medium"
            >
              Blueprint
            </Link>
          }
        />

        {loading && (
          <GlassCard className="mt-8 p-8 text-sm text-muted-foreground">
            Loading mission…
          </GlassCard>
        )}
        {error && <GlassCard className="mt-8 p-5 text-sm text-destructive">{error}</GlassCard>}

        {mission && (
          <div className="mt-8 space-y-5">
            <GlassCard className="p-6">
              <div className="flex flex-wrap items-center gap-2">
                <MissionStatusBadge status={mission.status} />
                <Badge variant="outline">
                  {countryLabel(mission.country_code, mission.country_name)}
                </Badge>
                {(activeApprovedBlueprint ?? latestBlueprint) && (
                  <Badge variant="secondary">
                    Blueprint v{(activeApprovedBlueprint ?? latestBlueprint)?.version}
                    {activeApprovedBlueprint
                      ? " · approved"
                      : ` · ${latestBlueprint?.status ?? "draft"}`}
                  </Badge>
                )}
              </div>
              <p className="mt-3 text-sm text-muted-foreground">
                {activeApprovedBlueprint
                  ? "An approved Country Blueprint is ready. Start a campaign or open an existing one."
                  : latestBlueprint
                    ? "Review and approve a Country Blueprint before starting a campaign."
                    : "Generate a Country Blueprint to begin."}
              </p>
              <div className="mt-4 flex flex-wrap gap-2">
                <Button
                  type="button"
                  variant="outline"
                  onClick={() =>
                    void navigate({
                      to: "/scraping/$missionId/blueprint",
                      params: { missionId },
                    })
                  }
                >
                  {activeApprovedBlueprint || latestBlueprint
                    ? "Open blueprint"
                    : "Generate blueprint"}
                </Button>
                {canStartScrape && (
                  <Button
                    type="button"
                    disabled={starting}
                    onClick={() => void handleStartScrape()}
                  >
                    {starting ? "Starting…" : "Start scrape"}
                  </Button>
                )}
              </div>
            </GlassCard>

            {featuredCampaign && (
              <GlassCard className="p-6">
                <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
                  <div>
                    <h2 className="text-base font-semibold">
                      {activeCampaign ? "Active campaign" : "Latest campaign"}
                    </h2>
                    <p className="mt-2 text-sm capitalize text-muted-foreground">
                      {campaignStatusLabel(featuredCampaign.status)}
                      {featuredCampaign.blueprint_version_snapshot != null
                        ? ` · Blueprint v${featuredCampaign.blueprint_version_snapshot}`
                        : ""}
                      {isCampaignPollingStatus(featuredCampaign.status)
                        ? " · in progress"
                        : ""}
                    </p>
                  </div>
                  <Button
                    type="button"
                    onClick={() =>
                      void navigate({
                        to: "/scraping/$missionId/campaigns/$executionId",
                        params: { missionId, executionId: featuredCampaign.id },
                      })
                    }
                  >
                    Open campaign
                  </Button>
                </div>
              </GlassCard>
            )}

            {campaigns.length > 0 && (
              <GlassCard className="p-6">
                <h2 className="text-base font-semibold">Campaign history</h2>
                <div className="mt-4 space-y-2">
                  {campaigns.map((campaign) => (
                    <button
                      key={campaign.id}
                      type="button"
                      className="flex w-full items-center justify-between rounded-lg border border-border px-3 py-2 text-left text-sm hover:bg-accent"
                      onClick={() =>
                        void navigate({
                          to: "/scraping/$missionId/campaigns/$executionId",
                          params: { missionId, executionId: campaign.id },
                        })
                      }
                    >
                      <span className="capitalize text-muted-foreground">
                        {campaignStatusLabel(campaign.status)}
                      </span>
                      <span className="text-xs text-muted-foreground">
                        {new Date(campaign.created_at).toLocaleString()}
                      </span>
                    </button>
                  ))}
                </div>
              </GlassCard>
            )}
          </div>
        )}
      </div>
    </AppShell>
  );
}

function existingCampaignConflictDetails(
  error: unknown,
): ScrapingExecutionConflictDetails | null {
  if (!(error instanceof ApiClientError) || error.status !== 409) {
    return null;
  }
  const details = error.body?.details;
  if (
    typeof details !== "object" ||
    details === null ||
    !("existing_execution_id" in details) ||
    typeof details.existing_execution_id !== "string"
  ) {
    return null;
  }
  return details as ScrapingExecutionConflictDetails;
}
