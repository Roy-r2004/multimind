import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { GlassCard, PageHeader } from "@/components/cinematic/PageChrome";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";
import { getScrapingExecution } from "@/lib/scraping/api";
import { campaignStatusLabel } from "@/lib/scraping/campaignCockpit";
import { isMissionCampaignExecution } from "@/lib/scraping/missionHub";
import type { ScrapingExecutionSummary } from "@/lib/scraping/types";

/**
 * Legacy path: /scraping/:missionId/executions/:executionId
 *
 * Mission-campaign executions redirect to the new cockpit.
 * Genuinely legacy executions stay read-only — no scrape start, no mutation of campaigns.
 */
export const Route = createFileRoute("/scraping/$missionId/executions/$executionId")({
  head: () => ({ meta: [{ title: "Legacy Execution - MultiAI" }] }),
  component: LegacyOrRedirectExecutionPage,
});

function LegacyOrRedirectExecutionPage() {
  const { missionId, executionId } = Route.useParams();
  const { authHeaders } = useAuth();
  const navigate = useNavigate();
  const [legacy, setLegacy] = useState<ScrapingExecutionSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const auth = authHeaders();
    if (!auth) {
      void navigate({ to: "/login" });
      return;
    }
    let cancelled = false;
    void getScrapingExecution(auth, executionId)
      .then((detail) => {
        if (cancelled) return;
        const execution = detail.execution;
        if (isMissionCampaignExecution(execution)) {
          void navigate({
            to: "/scraping/$missionId/campaigns/$executionId",
            params: { missionId, executionId },
            replace: true,
          });
          return;
        }
        setLegacy(execution);
        setLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Failed to load execution.");
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [authHeaders, executionId, missionId, navigate]);

  return (
    <AppShell>
      <div className="mx-auto max-w-3xl px-6 py-10">
        <PageHeader
          eyebrow="Scraping Mission"
          title="Legacy execution"
          description="This path no longer starts scrapes. Campaign work uses the blueprint-driven cockpit."
          action={
            <Link
              to="/scraping/$missionId"
              params={{ missionId }}
              className="rounded-xl border border-border px-4 py-2.5 text-sm font-medium"
            >
              Back to mission
            </Link>
          }
        />
        {loading && (
          <GlassCard className="mt-8 p-8 text-sm text-muted-foreground">
            Checking execution…
          </GlassCard>
        )}
        {error && <GlassCard className="mt-8 p-5 text-sm text-destructive">{error}</GlassCard>}
        {legacy && (
          <GlassCard className="mt-8 space-y-4 p-6">
            <p className="text-sm text-muted-foreground">
              This execution belongs to the previous scrape workflow. Historical records are
              preserved, but new work continues from the mission page and campaign cockpit.
            </p>
            <div className="text-sm">
              <div>
                Status:{" "}
                <span className="capitalize">{campaignStatusLabel(legacy.status)}</span>
              </div>
              <div className="mt-1 text-muted-foreground">
                Type: {legacy.execution_type}
                {legacy.mode ? ` · ${legacy.mode}` : ""}
              </div>
            </div>
            <Button
              type="button"
              onClick={() =>
                void navigate({
                  to: "/scraping/$missionId",
                  params: { missionId },
                })
              }
            >
              Return to mission
            </Button>
          </GlassCard>
        )}
      </div>
    </AppShell>
  );
}
