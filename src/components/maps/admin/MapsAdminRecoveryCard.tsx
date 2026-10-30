import { Loader2, RefreshCw, RotateCcw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { GlassCard } from "@/components/cinematic/PageChrome";
import { useAuth } from "@/lib/auth";
import {
  getMapsCensusEligibilityRetryProjection,
  getMapsCensusKeepDropRetryProjection,
  retryMapsCensusFailedKeepDrop,
  retryMapsCensusUnresolvedEligibilityEvidence,
} from "@/lib/maps/adminApi";
import type {
  MapsEligibilityRetryProjection,
  MapsKeepDropRetryProjection,
} from "@/lib/maps/adminTypes";
import { cn } from "@/lib/utils";

type RecoveryConfirmKind = "keep_drop" | "eligibility" | null;

/**
 * "Scraper Recovery" controls for a Maps census run: retry technical
 * keep/drop failures (classifier_unavailable — never a real decision) and
 * re-run Phase 2 for places with unresolved inpatient/residential
 * evidence.
 *
 * Self-contained by design — it fetches its own projections and owns its
 * own confirm dialog/busy/error state — specifically so it can be dropped
 * into BOTH the admin dashboard (MapsCampaignAdminPage) and the main run
 * results page (routes/maps.$runId.tsx) without either page needing to
 * know anything about recovery, and without duplicating this logic in two
 * places.
 *
 * Authorization: any authenticated OWNER/ADMIN/MEMBER of the run's own
 * organization can use these actions (the backend endpoints use
 * get_auth_context, not require_org_admin — recovering a stalled scrape is
 * a normal-user action, not an admin-only one). Organization isolation is
 * still fully enforced server-side regardless of role. This component
 * simply renders nothing until at least one projection loads successfully,
 * so a genuinely unauthorized viewer (unauthenticated, or a member of a
 * different organization — both still rejected server-side) never sees it.
 */
export function MapsAdminRecoveryCard({ runId }: { runId: string }) {
  const { authHeaders } = useAuth();
  const [keepDropProjection, setKeepDropProjection] = useState<MapsKeepDropRetryProjection | null>(
    null,
  );
  const [eligibilityProjection, setEligibilityProjection] =
    useState<MapsEligibilityRetryProjection | null>(null);
  const [visible, setVisible] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState<string | null>(null);
  const [confirmKind, setConfirmKind] = useState<RecoveryConfirmKind>(null);

  const loadProjections = useCallback(async () => {
    const auth = authHeaders();
    if (!auth) return;
    setLoading(true);
    try {
      const [keepDrop, eligibility] = await Promise.all([
        getMapsCensusKeepDropRetryProjection(auth, runId),
        getMapsCensusEligibilityRetryProjection(auth, runId),
      ]);
      setKeepDropProjection(keepDrop);
      setEligibilityProjection(eligibility);
      // Either endpoint responding at all (even with zero candidates)
      // proves this viewer belongs to this run's organization — a 401
      // (unauthenticated) or 404 (different organization) would have
      // thrown and left `visible` false instead.
      setVisible(true);
      setError(null);
    } catch (err) {
      setKeepDropProjection(null);
      setEligibilityProjection(null);
      setVisible(false);
      setError(err instanceof Error ? err.message : "Failed to load recovery projections");
    } finally {
      setLoading(false);
    }
  }, [authHeaders, runId]);

  useEffect(() => {
    void loadProjections();
  }, [loadProjections]);

  async function runRecoveryAction(key: string, fn: () => Promise<unknown>) {
    const auth = authHeaders();
    if (!auth) return;
    setActionBusy(key);
    try {
      await fn();
      await loadProjections();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Action failed");
    } finally {
      setActionBusy(null);
      setConfirmKind(null);
    }
  }

  // Never shown to someone unauthorized for this run (unauthenticated, or
  // a different organization), and never shown while we don't yet know —
  // only an unauthorized-safe "not yet loaded" state, not an error flash.
  if (!visible && !loading) return null;

  return (
    <>
      <GlassCard className="mt-4 p-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-sm font-semibold">Scraper Recovery</h2>
            <p className="text-xs text-muted-foreground">
              Re-queue places lost to a technical failure, not a real classification decision.
            </p>
          </div>
          <Button
            variant="ghost"
            size="sm"
            title="Reload recovery counts"
            disabled={!!actionBusy}
            onClick={() => void loadProjections()}
          >
            <RefreshCw className={cn("size-4", loading && "animate-spin")} />
          </Button>
        </div>

        {error && <p className="mt-2 text-xs text-destructive">{error}</p>}

        <div className="mt-3 grid gap-4 sm:grid-cols-2">
          <div className="rounded-lg border border-border/60 p-3">
            <p className="text-xs text-muted-foreground">
              Technical keep/drop failures eligible for retry
            </p>
            <p className="mt-1 text-2xl font-semibold">
              {keepDropProjection ? keepDropProjection.candidate_count : "—"}
            </p>
            {keepDropProjection && (
              <p className="mt-1 text-xs text-muted-foreground">
                Up to {keepDropProjection.max_total_calls_upper_bound} AI calls (upper bound, not
                an actual cost)
                {keepDropProjection.keep_drop_already_running &&
                  " · a keep/drop pass is already running"}
              </p>
            )}
            <Button
              className="mt-3"
              variant="outline"
              size="sm"
              disabled={
                !!actionBusy ||
                !keepDropProjection ||
                keepDropProjection.candidate_count === 0 ||
                keepDropProjection.keep_drop_already_running
              }
              onClick={() => setConfirmKind("keep_drop")}
            >
              <RotateCcw className="size-4" />
              Retry Failed Keep/Drop
            </Button>
          </div>

          <div className="rounded-lg border border-border/60 p-3">
            <p className="text-xs text-muted-foreground">
              Unresolved Phase 2 inpatient/residential evidence
            </p>
            <p className="mt-1 text-2xl font-semibold">
              {eligibilityProjection ? eligibilityProjection.candidate_count : "—"}
            </p>
            {eligibilityProjection && (
              <p className="mt-1 text-xs text-muted-foreground">
                Up to {eligibilityProjection.max_total_calls_upper_bound} AI calls (upper bound;
                Sonar capped at {eligibilityProjection.max_sonar_fallback_calls_for_whole_run} for
                the whole run)
              </p>
            )}
            <Button
              className="mt-3"
              variant="outline"
              size="sm"
              disabled={
                !!actionBusy ||
                !eligibilityProjection ||
                eligibilityProjection.candidate_count === 0
              }
              onClick={() => setConfirmKind("eligibility")}
            >
              <RotateCcw className="size-4" />
              Retry Unresolved Phase 2
            </Button>
          </div>
        </div>
      </GlassCard>

      <AlertDialog open={!!confirmKind} onOpenChange={(open) => !open && setConfirmKind(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {confirmKind === "keep_drop" && "Retry failed keep/drop judgments?"}
              {confirmKind === "eligibility" && "Retry unresolved Phase 2 evidence?"}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {confirmKind === "keep_drop" &&
                `This re-judges ${keepDropProjection?.candidate_count ?? 0} place(s) that were ` +
                  `never actually classified — only a technical/provider failure, not a real ` +
                  `decision. This makes PAID AI calls: up to ` +
                  `${keepDropProjection?.max_total_calls_upper_bound ?? "an unbounded number of"} ` +
                  `calls (upper bound, not an actual cost). Genuine keep/drop decisions are never touched.`}
              {confirmKind === "eligibility" &&
                `This re-runs Phase 2 eligibility research for ` +
                  `${eligibilityProjection?.candidate_count ?? 0} place(s) whose inpatient/` +
                  `residential evidence is still unresolved. This makes PAID AI calls: up to ` +
                  `${eligibilityProjection?.max_total_calls_upper_bound ?? "an unbounded number of"} ` +
                  `calls (upper bound, not an actual cost). Already-confirmed decisions are never ` +
                  `downgraded.`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={!!actionBusy}>Keep as is</AlertDialogCancel>
            <AlertDialogAction
              disabled={!!actionBusy}
              onClick={(e) => {
                e.preventDefault();
                const auth = authHeaders();
                if (!auth || !confirmKind) return;
                if (confirmKind === "keep_drop") {
                  void runRecoveryAction("keep_drop", () =>
                    retryMapsCensusFailedKeepDrop(auth, runId),
                  );
                } else if (confirmKind === "eligibility") {
                  void runRecoveryAction("eligibility", () =>
                    retryMapsCensusUnresolvedEligibilityEvidence(auth, runId),
                  );
                }
              }}
            >
              {actionBusy ? <Loader2 className="size-4 animate-spin" /> : "Confirm"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
