import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { GlassCard, PageHeader } from "@/components/cinematic/PageChrome";
import { BlueprintApprovalBar } from "@/components/scraping/BlueprintApprovalBar";
import { BlueprintViewer } from "@/components/scraping/BlueprintViewer";
import { MissionStatusBadge } from "@/components/scraping/MissionStatusBadge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useAuth } from "@/lib/auth";
import {
  approveScrapingBlueprint,
  generateScrapingBlueprint,
  getScrapingMission,
  listScrapingBlueprints,
  rejectScrapingBlueprint,
  requestScrapingBlueprintChanges,
} from "@/lib/scraping/api";
import type { ScrapingBlueprint, ScrapingMissionDetail } from "@/lib/scraping/types";

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
        return;
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
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load blueprint");
      } finally {
        setLoading(false);
      }
    },
    [authHeaders, missionId, navigate],
  );

  useEffect(() => {
    void load();
  }, [load]);

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

  return (
    <AppShell>
      <div className="mx-auto max-w-5xl px-6 py-10">
        <PageHeader
          eyebrow="Scraping Council"
          title={mission?.title ?? "Blueprint"}
          description="Review the generated blueprint before approval."
        />
        {loading && (
          <GlassCard className="mt-8 p-8 text-sm text-muted-foreground">
            Loading blueprint...
          </GlassCard>
        )}
        {error && <GlassCard className="mt-8 p-8 text-sm text-destructive">{error}</GlassCard>}
        {success && !error && (
          <GlassCard className="mt-8 p-4 text-sm text-primary">{success}</GlassCard>
        )}
        {!loading && !error && !selected && (
          <GlassCard className="mt-8 p-12 text-center text-sm text-muted-foreground">
            No blueprint versions yet.
          </GlassCard>
        )}
        {selected && (
          <div className="mt-8 space-y-5">
            <GlassCard className="p-5">
              <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
                <div className="space-y-2 text-sm">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">Blueprint version v{selected.version}</span>
                    <MissionStatusBadge status={selected.status} />
                  </div>
                  <div className="text-muted-foreground">
                    Created {new Date(selected.created_at).toLocaleString()}
                  </div>
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
                  {selected.rejection_reason && (
                    <div className="text-destructive">Reason: {selected.rejection_reason}</div>
                  )}
                </div>
                <Select value={selected.id} onValueChange={setSelectedId}>
                  <SelectTrigger className="w-full md:w-56">
                    <SelectValue placeholder="Blueprint version" />
                  </SelectTrigger>
                  <SelectContent>
                    {blueprints.map((blueprint) => (
                      <SelectItem key={blueprint.id} value={blueprint.id}>
                        v{blueprint.version} · {blueprint.status}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </GlassCard>
            {selected.blueprint_json ? (
              <BlueprintViewer content={selected.blueprint_json} />
            ) : (
              <GlassCard className="p-8 text-sm text-muted-foreground">
                Blueprint content is not available.
              </GlassCard>
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
      </div>
    </AppShell>
  );
}
