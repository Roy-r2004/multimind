import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useCallback, useEffect, useRef, useState } from "react";
import { CheckCircle2, Loader2, RefreshCw } from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { PageHeader } from "@/components/cinematic/PageChrome";
import { PlaybookGeneratePanel } from "@/components/playbooks/PlaybookGeneratePanel";
import { PlaybookObservationGroups } from "@/components/playbooks/PlaybookObservationGroups";
import { PlaybookProgressCard } from "@/components/playbooks/PlaybookProgressCard";
import { PlaybookSummaryCard } from "@/components/playbooks/PlaybookSummaryCard";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { ApiClientError } from "@/lib/api/types";
import type {
  ApiPlaybook,
  ApiPlaybookObservation,
  ApiPlaybookPending,
  ApiPlaybookRun,
} from "@/lib/api/types";
import { useAuth } from "@/lib/auth";
import {
  PLAYBOOK_PAGE_SUBTITLE,
  PLAYBOOK_PAGE_TITLE,
  PLAYBOOK_POLL_INTERVAL_MS,
  derivePlaybookPageState,
  playbookShowsRetryButton,
  shouldApplyPolledRun,
  shouldFetchPlaybookObservations,
  shouldRefetchPlaybookAfterRun,
  shouldResumePlaybookPolling,
  shouldStopPollingForStatus,
} from "@/lib/playbooks";

export const Route = createFileRoute("/playbooks")({
  head: () => ({ meta: [{ title: "My Playbooks — MultiAI" }] }),
  component: PlaybooksPage,
});

function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message.trim()) return error.message;
  return fallback;
}

function PlaybooksPage() {
  const navigate = useNavigate();
  const { authHeaders, isAuthenticated, isLoading: authLoading } = useAuth();
  const [playbook, setPlaybook] = useState<ApiPlaybook | null>(null);
  const [run, setRun] = useState<ApiPlaybookRun | null>(null);
  const [observations, setObservations] = useState<ApiPlaybookObservation[]>([]);
  const [pending, setPending] = useState<ApiPlaybookPending | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [observationError, setObservationError] = useState<string | null>(null);
  const [runLoadError, setRunLoadError] = useState<string | null>(null);
  const [generateError, setGenerateError] = useState<string | null>(null);
  const [pollError, setPollError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);

  const expectedRunIdRef = useRef<string | null>(null);
  const pollInFlightRef = useRef(false);
  const submittingRef = useRef(false);

  const pageState = derivePlaybookPageState({ loading, loadError, playbook, run });

  const loadObservations = useCallback(
    async (current: ApiPlaybook) => {
      const auth = authHeaders();
      if (!auth || !shouldFetchPlaybookObservations(current)) {
        setObservations([]);
        setObservationError(null);
        return;
      }
      try {
        const rows = await api.playbooks.listObservations(auth);
        setObservations(rows);
        setObservationError(null);
      } catch (error) {
        setObservationError(errorMessage(error, "Could not load Playbook observations."));
      }
    },
    [authHeaders],
  );

  const refreshCompleted = useCallback(async () => {
    const auth = authHeaders();
    if (!auth) return;
    try {
      const [nextPlaybook, latestRun, nextPending] = await Promise.all([
        api.playbooks.getMine(auth),
        api.playbooks.getLatestRun(auth),
        api.playbooks.getPending(auth),
      ]);
      setPlaybook(nextPlaybook);
      if (latestRun) setRun(latestRun);
      setPending(nextPending);
      await loadObservations(nextPlaybook);
    } catch (error) {
      setLoadError(errorMessage(error, "Could not refresh the Playbook."));
    }
  }, [authHeaders, loadObservations]);

  useEffect(() => {
    if (authLoading) return;
    const auth = authHeaders();
    if (!auth || !isAuthenticated) {
      void navigate({ to: "/login" });
      return;
    }

    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    const requestAuth = auth;

    async function load() {
      try {
        const [playbookResult, runResult] = await Promise.allSettled([
          api.playbooks.getMine(requestAuth),
          api.playbooks.getLatestRun(requestAuth),
        ]);
        if (cancelled) return;
        if (playbookResult.status === "rejected") {
          setLoadError(errorMessage(playbookResult.reason, "Could not load your Playbook."));
          return;
        }
        const nextPlaybook = playbookResult.value;
        setPlaybook(nextPlaybook);
        setLoadError(null);
        if (runResult.status === "fulfilled") {
          setRun(runResult.value);
          expectedRunIdRef.current = shouldResumePlaybookPolling(runResult.value)
            ? (runResult.value?.id ?? null)
            : null;
          setRunLoadError(null);
        } else {
          setRunLoadError(
            errorMessage(runResult.reason, "Could not load the latest Playbook run."),
          );
        }
        await loadObservations(nextPlaybook);
        if (shouldFetchPlaybookObservations(nextPlaybook)) {
          try {
            setPending(await api.playbooks.getPending(requestAuth));
          } catch {
            setPending(null);
          }
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [authHeaders, authLoading, isAuthenticated, loadObservations, navigate, reloadToken]);

  useEffect(() => {
    if (!shouldResumePlaybookPolling(run) || !run) {
      expectedRunIdRef.current = null;
      return;
    }

    const auth = authHeaders();
    if (!auth) return;

    expectedRunIdRef.current = run.id;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const requestAuth = auth;

    async function poll() {
      if (cancelled || pollInFlightRef.current) return;
      const expectedId = expectedRunIdRef.current;
      if (!expectedId) return;
      pollInFlightRef.current = true;
      try {
        const next = await api.playbooks.getRun(requestAuth, expectedId);
        if (cancelled) return;
        if (shouldApplyPolledRun(expectedRunIdRef.current, next)) {
          setRun(next);
          setPollError(null);
          if (shouldRefetchPlaybookAfterRun(next)) {
            expectedRunIdRef.current = null;
            await refreshCompleted();
            return;
          }
          if (next.status === "failed") {
            expectedRunIdRef.current = null;
            return;
          }
        }
      } catch (error) {
        if (cancelled) return;
        const status = error instanceof ApiClientError ? error.status : 0;
        if (shouldStopPollingForStatus(status)) {
          expectedRunIdRef.current = null;
          setPollError(errorMessage(error, "Playbook progress is no longer available."));
          return;
        }
        setPollError("Progress update delayed. Retrying…");
      } finally {
        pollInFlightRef.current = false;
      }
      if (!cancelled && expectedRunIdRef.current) {
        timer = setTimeout(poll, PLAYBOOK_POLL_INTERVAL_MS);
      }
    }

    timer = setTimeout(poll, PLAYBOOK_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
    // Polling is keyed to run identity and in-flight status so count ticks do not reset the loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authHeaders, refreshCompleted, run?.id, run?.status]);

  async function startGeneration() {
    const auth = authHeaders();
    if (!auth || submittingRef.current) return;
    submittingRef.current = true;
    setSubmitting(true);
    setGenerateError(null);
    try {
      const nextRun = await api.playbooks.generate(auth);
      expectedRunIdRef.current = nextRun.id;
      setRun(nextRun);
      setPollError(null);
    } catch (error) {
      setGenerateError(errorMessage(error, "Playbook generation could not be started."));
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  }

  async function startRerun() {
    const auth = authHeaders();
    if (!auth || submittingRef.current || !pending || pending.up_to_date) return;
    submittingRef.current = true;
    setSubmitting(true);
    setGenerateError(null);
    try {
      const nextRun = await api.playbooks.rerun(auth);
      expectedRunIdRef.current = nextRun.id;
      setRun(nextRun);
      setPollError(null);
    } catch (error) {
      setGenerateError(errorMessage(error, "Playbook rerun could not be started."));
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  }

  const failedWithoutPlaybook = pageState === "failed_without_playbook";
  const showFailedOnActive =
    playbook &&
    run?.status === "failed" &&
    pageState !== "failed_without_playbook" &&
    (pageState === "active" || pageState === "active_with_warnings");

  return (
    <AppShell>
      <div className="mx-auto max-w-7xl px-4 py-8 md:px-6">
        <PageHeader
          eyebrow="Playbooks"
          title={PLAYBOOK_PAGE_TITLE}
          description={PLAYBOOK_PAGE_SUBTITLE}
        />

        {pageState === "initial_loading" ? (
          <div className="flex items-center justify-center gap-2 py-24 text-sm text-muted-foreground">
            <Loader2 className="size-5 animate-spin" aria-hidden />
            <span>Loading your Playbook…</span>
          </div>
        ) : null}

        {pageState === "load_error" ? (
          <Alert variant="destructive" className="mt-8">
            <AlertTitle>Could not load Playbook</AlertTitle>
            <AlertDescription>
              <p>{loadError}</p>
              <Button className="mt-4" onClick={() => setReloadToken((value) => value + 1)}>
                Retry page load
              </Button>
            </AlertDescription>
          </Alert>
        ) : null}

        {runLoadError && playbook ? (
          <Alert className="mt-6">
            <AlertTitle>Latest run unavailable</AlertTitle>
            <AlertDescription>{runLoadError}</AlertDescription>
          </Alert>
        ) : null}

        {pageState === "not_generated" ? (
          <div className="mt-8">
            <PlaybookGeneratePanel
              submitting={submitting}
              error={generateError}
              onGenerate={() => void startGeneration()}
            />
          </div>
        ) : null}

        {pageState === "queued" || pageState === "processing" ? (
          <div className="mt-8">
            {run ? <PlaybookProgressCard run={run} pollError={pollError} /> : null}
          </div>
        ) : null}

        {failedWithoutPlaybook && run ? (
          <Alert variant="destructive" className="mt-8">
            <AlertTitle>Playbook generation failed</AlertTitle>
            <AlertDescription>
              <p>{run.error_message || "Playbook generation failed."}</p>
              {generateError ? <p className="mt-2">{generateError}</p> : null}
              {playbookShowsRetryButton(pageState) ? (
                <Button
                  className="mt-4"
                  onClick={() => void startGeneration()}
                  disabled={submitting}
                  aria-label="Retry Generation"
                >
                  {submitting ? <Loader2 className="size-4 animate-spin" aria-hidden /> : null}
                  Retry Generation
                </Button>
              ) : null}
            </AlertDescription>
          </Alert>
        ) : null}

        {pageState === "active" || pageState === "active_with_warnings" ? (
          <div className="mt-8 space-y-7">
            {run && (run.status === "queued" || run.status === "processing") ? (
              <PlaybookProgressCard run={run} pollError={pollError} />
            ) : null}
            {showFailedOnActive && run ? (
              <Alert>
                <AlertTitle>A later generation attempt failed</AlertTitle>
                <AlertDescription>
                  {run.error_message || "The latest run failed. Your saved Playbook is unchanged."}
                </AlertDescription>
              </Alert>
            ) : null}
            {playbook ? (
              <>
                <div className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-border/80 bg-card/80 px-5 py-4 shadow-sm">
                  <div className="flex items-start gap-3 text-sm">
                    <span
                      className={`mt-0.5 grid size-8 shrink-0 place-items-center rounded-full ${pending?.up_to_date ? "bg-emerald-50 text-emerald-700" : "bg-primary/10 text-primary"}`}
                    >
                      {pending?.up_to_date ? (
                        <CheckCircle2 className="size-4" aria-hidden />
                      ) : (
                        <RefreshCw className="size-4" aria-hidden />
                      )}
                    </span>
                    <div>
                      <p className="font-semibold text-foreground">
                        {pending?.up_to_date
                          ? "Your Playbook is up to date"
                          : `${pending?.pending_source_items ?? 0} pending source update(s)`}
                      </p>
                      {pending && !pending.up_to_date ? (
                        <span className="mt-1 block text-xs leading-relaxed text-muted-foreground">
                          {pending.new_chats} new chats · {pending.new_turns} new ·{" "}
                          {pending.changed_turns} changed · {pending.removed_turns} removed ·{" "}
                          {pending.brain_changes} Brain
                        </span>
                      ) : null}
                    </div>
                  </div>
                  <Button
                    variant={pending?.up_to_date ? "outline" : "default"}
                    onClick={() => void startRerun()}
                    disabled={submitting || !pending || pending.up_to_date}
                  >
                    {submitting ? <Loader2 className="size-4 animate-spin" aria-hidden /> : null}{" "}
                    Rerun Playbook
                  </Button>
                </div>
                {generateError ? (
                  <Alert variant="destructive">
                    <AlertDescription>{generateError}</AlertDescription>
                  </Alert>
                ) : null}
                <PlaybookSummaryCard
                  playbook={playbook}
                  run={run}
                  observationCount={observations.length}
                />
              </>
            ) : null}
            <div className="pt-3">
              <div className="mb-6 border-b border-border/70 pb-4">
                <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-primary">
                  Supporting detail
                </p>
                <h2 className="mt-1 font-display text-2xl font-semibold tracking-tight">
                  Observations
                </h2>
                <p className="mt-1.5 max-w-2xl text-sm text-muted-foreground">
                  The evidence and recurring signals behind your Playbook summary.
                </p>
              </div>
              {observationError ? (
                <Alert variant="destructive">
                  <AlertTitle>Could not load observations</AlertTitle>
                  <AlertDescription>
                    <p>{observationError}</p>
                    {playbook ? (
                      <Button className="mt-4" onClick={() => void loadObservations(playbook)}>
                        Retry observations
                      </Button>
                    ) : null}
                  </AlertDescription>
                </Alert>
              ) : (
                <PlaybookObservationGroups observations={observations} />
              )}
            </div>
          </div>
        ) : null}
      </div>
    </AppShell>
  );
}
