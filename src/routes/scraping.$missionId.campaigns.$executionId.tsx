import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { GlassCard, PageHeader } from "@/components/cinematic/PageChrome";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";
import {
  cancelMissionCampaign,
  getMissionCampaign,
  listMissionCampaignEvents,
  pauseMissionCampaign,
  resumeMissionCampaign,
} from "@/lib/scraping/api";
import {
  applyCampaignControlSummary,
  campaignActionFlags,
  campaignStatusLabel,
  clarificationNeedsReviewMessage,
  clarificationStatusLabel,
  isCampaignPollingStatus,
  latestCampaignSequence,
  mergeCampaignEvents,
} from "@/lib/scraping/campaignCockpit";
import {
  friendlyCampaignEventMessage,
  friendlyCampaignStageLabel,
} from "@/lib/scraping/blueprintReviewPresentation";
import type {
  ScrapingEvent,
  ScrapingExecutionDetail,
  ScrapingExecutionSummary,
} from "@/lib/scraping/types";

const POLL_INTERVAL_MS = 2_000;

export const Route = createFileRoute("/scraping/$missionId/campaigns/$executionId")({
  head: () => ({ meta: [{ title: "Mission Campaign - MultiAI" }] }),
  component: MissionCampaignCockpit,
});

function MissionCampaignCockpit() {
  const { missionId, executionId } = Route.useParams();
  const { authHeaders } = useAuth();
  const navigate = useNavigate();
  const [detail, setDetail] = useState<ScrapingExecutionDetail | null>(null);
  const [events, setEvents] = useState<ScrapingEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [acting, setActing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const latestSequence = useRef(0);

  const refresh = useCallback(
    async (includeHistory = false) => {
      const auth = authHeaders();
      if (!auth) {
        void navigate({ to: "/login" });
        return;
      }
      const [nextDetail, nextEvents] = await Promise.all([
        getMissionCampaign(auth, missionId, executionId),
        listMissionCampaignEvents(
          auth,
          missionId,
          executionId,
          includeHistory ? undefined : latestSequence.current,
        ),
      ]);
      setDetail(nextDetail);
      setEvents((current) => {
        const merged = mergeCampaignEvents(includeHistory ? [] : current, nextEvents);
        latestSequence.current = latestCampaignSequence(merged);
        return merged;
      });
    },
    [authHeaders, executionId, missionId, navigate],
  );

  useEffect(() => {
    setLoading(true);
    setError(null);
    latestSequence.current = 0;
    void refresh(true)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load campaign."))
      .finally(() => setLoading(false));
  }, [refresh]);

  const status = detail?.execution.status;
  useEffect(() => {
    if (!status || !isCampaignPollingStatus(status)) return;
    const timer = window.setInterval(() => {
      void refresh().catch((err) =>
        setError(err instanceof Error ? err.message : "Failed to refresh campaign."),
      );
    }, POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [refresh, status]);

  const execution = detail?.execution;
  const progress = Math.min(100, Math.max(0, execution?.progress_percent ?? 0));
  const history = useMemo(() => [...events].reverse(), [events]);
  const actions = useMemo(
    () =>
      execution
        ? campaignActionFlags(execution.status, execution.clarification_status)
        : { canPause: false, canResume: false, canCancel: false },
    [execution],
  );

  async function control(
    action: (
      auth: { token: string; orgId: string },
      mission: string,
      campaign: string,
    ) => Promise<ScrapingExecutionSummary>,
  ) {
    const auth = authHeaders();
    if (!auth || acting) {
      if (!auth) void navigate({ to: "/login" });
      return;
    }
    setActing(true);
    setError(null);
    try {
      const summary = await action(auth, missionId, executionId);
      // Apply returned summary immediately so Resume/Pause/Cancel reflect persisted state
      // without staying on "Updating…" while the worker acknowledges.
      setDetail((current) => (current ? applyCampaignControlSummary(current, summary) : current));
      setActing(false);
      void refresh().catch((err) =>
        setError(err instanceof Error ? err.message : "Failed to refresh campaign."),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Campaign control failed.");
      setActing(false);
    }
  }

  return (
    <AppShell>
      <div className="mx-auto max-w-6xl px-6 py-10">
        <PageHeader
          eyebrow="Scraping Mission"
          title={friendlyCampaignStageLabel(execution?.current_stage_label) || "Campaign"}
          description={
            execution
              ? `${execution.country_name} · Country Blueprint v${execution.blueprint_version_snapshot ?? "—"}`
              : "Follow research-pipeline campaign progress."
          }
          action={
            <div className="flex flex-wrap gap-2">
              <Link
                to="/scraping/$missionId"
                params={{ missionId }}
                className="rounded-xl border border-border px-4 py-2.5 text-sm font-medium"
              >
                Back to mission
              </Link>
              <Link
                to="/scraping/$missionId/blueprint"
                params={{ missionId }}
                className="rounded-xl border border-border px-4 py-2.5 text-sm font-medium"
              >
                Blueprint
              </Link>
            </div>
          }
        />
        {loading && (
          <GlassCard className="mt-8 p-8 text-sm text-muted-foreground">
            Loading campaign cockpit…
          </GlassCard>
        )}
        {error && <GlassCard className="mt-8 p-5 text-sm text-destructive">{error}</GlassCard>}
        {detail && execution && (
          <div className="mt-8 space-y-5">
            <GlassCard className="p-6">
              <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant="secondary">
                      {friendlyCampaignStageLabel(execution.status_label)}
                    </Badge>
                    <span className="text-sm capitalize text-muted-foreground">
                      {campaignStatusLabel(execution.status)}
                    </span>
                    {clarificationStatusLabel(execution.clarification_status) && (
                      <Badge variant="outline">
                        {clarificationStatusLabel(execution.clarification_status)}
                      </Badge>
                    )}
                    {detail.mock && <Badge variant="outline">Test campaign</Badge>}
                  </div>
                  <p className="mt-3 text-sm text-muted-foreground">
                    {clarificationNeedsReviewMessage(execution.clarification_status) ||
                      friendlyCampaignEventMessage(
                        execution.latest_message ?? "Campaign is waiting for its next update.",
                      )}
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  {actions.canPause && (
                    <Button
                      type="button"
                      variant="outline"
                      disabled={acting}
                      onClick={() => void control(pauseMissionCampaign)}
                    >
                      {acting ? "Updating…" : "Pause"}
                    </Button>
                  )}
                  {actions.canResume && (
                    <Button
                      type="button"
                      disabled={acting}
                      onClick={() => void control(resumeMissionCampaign)}
                    >
                      {acting ? "Updating…" : "Resume"}
                    </Button>
                  )}
                  {actions.canCancel && (
                    <Button
                      type="button"
                      variant="destructive"
                      disabled={acting}
                      onClick={() => void control(cancelMissionCampaign)}
                    >
                      {acting ? "Updating…" : "Cancel"}
                    </Button>
                  )}
                </div>
              </div>
              <div className="mt-5 h-2 overflow-hidden rounded-full bg-muted">
                <div
                  className="h-full bg-primary transition-all"
                  style={{ width: `${progress}%` }}
                />
              </div>
              <div className="mt-2 flex justify-between text-xs text-muted-foreground">
                <span>{friendlyCampaignStageLabel(execution.current_stage_label)}</span>
                <span>{progress}% complete</span>
              </div>
            </GlassCard>

            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              <Metric
                label="Regions"
                value={`${execution.regions_completed ?? 0}/${execution.regions_total ?? 0}`}
              />
              <Metric
                label="Candidates"
                value={execution.candidates_discovered ?? execution.sources_discovered}
              />
              <Metric
                label="Pages visited"
                value={execution.pages_visited ?? execution.documents_found}
              />
              <Metric
                label="Verified"
                value={execution.verified_facilities ?? execution.records_verified}
              />
              <Metric label="Manual review" value={execution.manual_review_count ?? 0} />
              <Metric label="Excluded" value={execution.excluded_count ?? 0} />
              <Metric
                label="Merged duplicates"
                value={execution.duplicates_merged ?? execution.duplicates_detected}
              />
            </div>

            <div className="grid gap-5 lg:grid-cols-2">
              <GlassCard className="p-5">
                <h2 className="text-lg font-semibold">Current work</h2>
                <div className="mt-4 space-y-3">
                  {detail.agents.length === 0 ? (
                    <p className="text-sm text-muted-foreground">
                      No campaign agents have reported yet.
                    </p>
                  ) : (
                    detail.agents.map((agent) => (
                      <div key={agent.id} className="rounded-lg border border-border p-3 text-sm">
                        <div className="flex flex-wrap items-center gap-2">
                          <Badge variant="outline">{agent.status}</Badge>
                          <span className="font-medium">{agent.planned_agent_name}</span>
                        </div>
                        <p className="mt-1 text-muted-foreground">
                          {agent.current_action ?? agent.current_task_title ?? "Waiting"}
                        </p>
                      </div>
                    ))
                  )}
                </div>
              </GlassCard>

              <GlassCard className="p-5">
                <h2 className="text-lg font-semibold">Event history</h2>
                <div className="mt-4 max-h-[28rem] space-y-2 overflow-auto">
                  {history.length === 0 ? (
                    <p className="text-sm text-muted-foreground">No campaign events yet.</p>
                  ) : (
                    history.map((event) => (
                      <div key={event.id} className="rounded-lg border border-border p-3 text-sm">
                        <div className="flex flex-wrap justify-between gap-2">
                          <span className="font-medium">
                            {friendlyCampaignEventMessage(event.event_type.replaceAll("_", " "))}
                          </span>
                          <span className="text-xs text-muted-foreground">
                            {new Date(event.created_at).toLocaleString()}
                          </span>
                        </div>
                        <p className="mt-1 text-muted-foreground">
                          {friendlyCampaignEventMessage(event.message)}
                        </p>
                      </div>
                    ))
                  )}
                </div>
              </GlassCard>
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <GlassCard className="p-4">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 text-lg font-semibold">{value}</p>
    </GlassCard>
  );
}
