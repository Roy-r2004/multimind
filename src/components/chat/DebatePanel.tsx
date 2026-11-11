import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Ban, Loader2, MessagesSquare, ShieldAlert, X } from "lucide-react";
import { Modal } from "@/components/Modal";
import { MessageContent } from "@/components/chat/MessageContent";
import { VendorLogo } from "@/components/chat/VendorLogo";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { Badge } from "@/components/ui/badge";
import type { ApiDebateSession, ApiModelAnswer } from "@/lib/api/types";
import { ApiClientError } from "@/lib/api/types";
import {
  buildChallengeRows,
  debateUsageDisplay,
  DEBATE_MAX_ATTEMPTS_PER_TURN,
  groupRowsByTarget,
  isDebateStatusActive,
  isZeroChallengeDebate,
  type DebateChallengeRow,
} from "@/lib/debateState";
import { cancelDebate, loadDebateForTurn, startDebate, subscribeDebate } from "@/lib/debateRunner";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

type ModelLookup = (id: string) => { name: string; color: string; vendor: string };

const STATUS_LABEL: Record<string, string> = {
  pending: "Starting",
  running: "In progress",
  completed: "Complete",
  partial: "Partial",
  failed: "Failed",
  cancelled: "Cancelled",
};

function statusTone(status: string): string {
  switch (status) {
    case "completed":
      return "border-emerald-500/40 bg-emerald-500/10 text-emerald-700";
    case "partial":
      return "border-amber-500/40 bg-amber-500/10 text-amber-800";
    case "failed":
      return "border-destructive/40 bg-destructive/10 text-destructive";
    case "cancelled":
      return "border-muted-foreground/30 bg-muted/40 text-muted-foreground";
    default:
      return "border-primary/40 bg-primary/10 text-primary";
  }
}

function severityTone(severity: string): string {
  if (severity === "high") return "border-destructive/40 bg-destructive/10 text-destructive";
  if (severity === "medium") return "border-amber-500/40 bg-amber-500/10 text-amber-800";
  return "border-border bg-muted/40 text-muted-foreground";
}

function stanceLabel(stance: string): string {
  if (stance === "concedes") return "Conceded";
  if (stance === "revises") return "Revised";
  return "Defended";
}

/** Live progress copy built only from facts the session actually reports —
 * never a fabricated percentage. */
function progressSummary(session: ApiDebateSession): string {
  const total = session.model_ids.length;
  const challengeCalls = session.challenges?.length ?? 0;
  const responseCalls = session.responses?.length ?? 0;
  if (session.status === "pending") return "Starting debate…";
  if (session.status !== "running") return STATUS_LABEL[session.status] ?? session.status;
  if (challengeCalls < total) {
    return `Reviewing original answers — ${challengeCalls}/${total} model(s) done.`;
  }
  if (responseCalls === 0) {
    return "Peer review complete. Models are responding to challenges…";
  }
  return `Responses in progress — ${responseCalls} model(s) replied so far.`;
}

function ChallengeCard({ row, modelById }: { row: DebateChallengeRow; modelById: ModelLookup }) {
  const challenger = modelById(row.challengerModelId);
  const target = modelById(row.targetModelId);
  const response = row.response;
  return (
    <AccordionItem value={row.key} className="rounded-xl border border-border/70 bg-card/60 px-3">
      <AccordionTrigger className="py-3 text-left hover:no-underline">
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2 pr-2">
          <span className="inline-flex items-center gap-1.5 text-xs font-semibold">
            <VendorLogo vendor={challenger.vendor} className="size-4" title={challenger.name} />
            {challenger.name}
          </span>
          <span className="text-muted-foreground text-xs">challenged</span>
          <span className="inline-flex items-center gap-1.5 text-xs font-semibold">
            <VendorLogo vendor={target.vendor} className="size-4" title={target.name} />
            {target.name}
          </span>
          <Badge
            variant="outline"
            className={cn("ml-1 text-[10px]", severityTone(row.challenge.severity))}
          >
            {row.challenge.severity}
          </Badge>
          {response ? (
            <Badge variant="outline" className="text-[10px]">
              {stanceLabel(response.stance)}
            </Badge>
          ) : (
            <Badge variant="outline" className="text-[10px] text-muted-foreground">
              Awaiting response
            </Badge>
          )}
          <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
            {row.challenge.issue}
          </span>
        </div>
      </AccordionTrigger>
      <AccordionContent className="space-y-3 pb-3">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
            Criticism
          </p>
          <MessageContent compact muted>
            {row.challenge.reasoning}
          </MessageContent>
        </div>
        {row.challenge.uncertainty ? (
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
              Flagged uncertainty
            </p>
            <MessageContent compact muted>
              {row.challenge.uncertainty}
            </MessageContent>
          </div>
        ) : null}
        {response ? (
          <div className="rounded-lg border border-border/60 bg-muted/20 p-3">
            <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
              {target.name}'s response — {stanceLabel(response.stance)}
            </p>
            <MessageContent compact>{response.explanation}</MessageContent>
            {response.updated_position ? (
              <div className="mt-2">
                <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                  Updated position
                </p>
                <MessageContent compact muted>
                  {response.updated_position}
                </MessageContent>
              </div>
            ) : null}
          </div>
        ) : (
          <p className="text-xs italic text-muted-foreground">
            {target.name} has not responded to this challenge yet.
          </p>
        )}
      </AccordionContent>
    </AccordionItem>
  );
}

function FindingsSection({
  session,
  modelById,
}: {
  session: ApiDebateSession;
  modelById: ModelLookup;
}) {
  const unchallenged = session.unchallenged_model_ids ?? [];
  const agreements = session.agreements ?? [];
  const unresolved = session.unresolved_disagreements ?? [];
  const corrections = session.corrections ?? [];
  const risks = session.risks ?? [];
  const uncertainties = session.uncertainties ?? [];
  const zeroChallenge = isZeroChallengeDebate(session);

  if (zeroChallenge && session.status !== "running" && session.status !== "pending") {
    return (
      <div className="rounded-xl border border-border/70 bg-muted/20 p-4 text-sm text-muted-foreground">
        No model raised a challenge against any other answer in this debate. That means peer review
        found nothing to contest — it does not by itself confirm the answers are correct.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {session.final_summary ? (
        <p className="rounded-lg border border-border/60 bg-muted/20 p-3 text-sm text-muted-foreground">
          {session.final_summary}
        </p>
      ) : null}

      {agreements.length > 0 ? (
        <FindingsGroup title="Explicit agreements" tone="emerald">
          <p className="text-xs text-muted-foreground">
            {agreements.length} point(s) where a model explicitly stated agreement.
          </p>
        </FindingsGroup>
      ) : null}

      {unchallenged.length > 0 ? (
        <FindingsGroup title="Unchallenged positions" tone="default">
          <p className="mb-2 text-xs text-muted-foreground">
            No challenge was raised against these answers — this reflects an absence of objection,
            not confirmed agreement.
          </p>
          <div className="flex flex-wrap gap-2">
            {unchallenged.map((id) => {
              const m = modelById(id);
              return (
                <span
                  key={id}
                  className="inline-flex items-center gap-1.5 rounded-full border border-border/60 bg-card px-2.5 py-1 text-xs"
                >
                  <VendorLogo vendor={m.vendor} className="size-3.5" title={m.name} />
                  {m.name}
                </span>
              );
            })}
          </div>
        </FindingsGroup>
      ) : null}

      {unresolved.length > 0 ? (
        <FindingsGroup title={`Unresolved disagreements (${unresolved.length})`} tone="amber">
          <ul className="space-y-1.5 text-xs">
            {unresolved.map((item) => (
              <li
                key={`${item.challenger_model_id}-${item.challenge_id}`}
                className="text-muted-foreground"
              >
                <span className="font-medium text-foreground">
                  {modelById(item.target_model_id).name}
                </span>{" "}
                still defends against{" "}
                <span className="font-medium text-foreground">
                  {modelById(item.challenger_model_id).name}
                </span>
                's challenge: {item.description}
              </li>
            ))}
          </ul>
        </FindingsGroup>
      ) : null}

      {corrections.length > 0 ? (
        <FindingsGroup title={`Revisions & concessions (${corrections.length})`} tone="emerald">
          <ul className="space-y-1.5 text-xs">
            {corrections.map((item) => (
              <li key={`${item.model_id}-${item.challenge_id}`} className="text-muted-foreground">
                <span className="font-medium text-foreground">{modelById(item.model_id).name}</span>{" "}
                {stanceLabel(item.stance).toLowerCase()}: {item.updated_position}
              </li>
            ))}
          </ul>
        </FindingsGroup>
      ) : null}

      {risks.length > 0 ? (
        <FindingsGroup
          title={`Risks raised (${risks.length})`}
          tone="red"
          icon={<ShieldAlert className="size-3.5" />}
        >
          <ul className="space-y-1.5 text-xs text-muted-foreground">
            {risks.map((item, i) => (
              <li key={i}>{item.description}</li>
            ))}
          </ul>
        </FindingsGroup>
      ) : null}

      {uncertainties.length > 0 ? (
        <FindingsGroup
          title={`Flagged uncertainties (${uncertainties.length})`}
          tone="default"
          icon={<AlertTriangle className="size-3.5" />}
        >
          <ul className="space-y-1.5 text-xs text-muted-foreground">
            {uncertainties.map((item, i) => (
              <li key={i}>{item.description}</li>
            ))}
          </ul>
        </FindingsGroup>
      ) : null}
    </div>
  );
}

function FindingsGroup({
  title,
  tone,
  icon,
  children,
}: {
  title: string;
  tone: "emerald" | "amber" | "red" | "default";
  icon?: React.ReactNode;
  children: React.ReactNode;
}) {
  const toneClass =
    tone === "emerald"
      ? "border-emerald-500/30"
      : tone === "amber"
        ? "border-amber-500/30"
        : tone === "red"
          ? "border-destructive/30"
          : "border-border/70";
  return (
    <div className={cn("rounded-xl border bg-card/60 p-3", toneClass)}>
      <p className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-foreground/80">
        {icon}
        {title}
      </p>
      {children}
    </div>
  );
}

function UsageSummary({ session }: { session: ApiDebateSession }) {
  const usage = debateUsageDisplay(session);
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-lg border border-border/60 bg-muted/15 px-3 py-2 text-xs text-muted-foreground">
      <span>
        Known cost:{" "}
        <span className="font-medium text-foreground">${usage.totalCostUsd.toFixed(4)}</span>
      </span>
      <span>
        Tokens: {usage.totalTokensInput.toLocaleString()} in /{" "}
        {usage.totalTokensOutput.toLocaleString()} out
      </span>
      {usage.hasUnknownUsage ? (
        <span className="text-amber-700">
          {usage.callsWithUnknownUsage} call(s) with unknown usage (not counted as zero)
        </span>
      ) : null}
    </div>
  );
}

export function DebateEligibilityButton({
  hasVerdict,
  completedAnswers,
  onClick,
  disabled,
}: {
  hasVerdict: boolean;
  completedAnswers: ApiModelAnswer[];
  onClick: () => void;
  disabled?: boolean;
}) {
  const eligible = hasVerdict && completedAnswers.length >= 2;
  const reason = !hasVerdict
    ? "Debate opens once the Final Verdict is ready."
    : completedAnswers.length < 2
      ? "Debate needs at least two completed AI answers to run a peer review."
      : "";
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={!eligible || disabled}
      title={eligible ? "Start an AI peer-review debate" : reason}
      className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-background/60 px-2.5 py-1.5 text-xs font-medium text-muted-foreground transition hover:bg-accent hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
    >
      <MessagesSquare className="size-3.5" /> Start Debate
    </button>
  );
}

export function DebatePanel({
  open,
  onClose,
  turnId,
  modelAnswers,
  modelById,
}: {
  open: boolean;
  onClose: () => void;
  turnId: string;
  modelAnswers: ApiModelAnswer[];
  modelById: ModelLookup;
}) {
  const { authHeaders } = useAuth();
  const [session, setSession] = useState<ApiDebateSession | null>(null);
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selectedAnswerId, setSelectedAnswerId] = useState<string | null>(
    modelAnswers[0]?.model_id ?? null,
  );
  const loadedTurnRef = useRef<string | null>(null);

  useEffect(() => {
    if (!open) return;
    const unsubscribe = subscribeDebate(turnId, setSession);
    if (loadedTurnRef.current !== turnId) {
      loadedTurnRef.current = turnId;
      setLoading(true);
      const auth = authHeaders();
      if (!auth) {
        setLoading(false);
        return unsubscribe;
      }
      loadDebateForTurn(auth, turnId)
        .catch((error) => {
          setLoadError(error instanceof Error ? error.message : "Failed to load debate state.");
        })
        .finally(() => setLoading(false));
    } else {
      setLoading(false);
    }
    return unsubscribe;
  }, [open, turnId, authHeaders]);

  const rows = useMemo(() => (session ? buildChallengeRows(session) : []), [session]);
  const rowsByTarget = useMemo(() => groupRowsByTarget(rows), [rows]);
  const active = session ? isDebateStatusActive(session.status) : false;

  async function handleStart() {
    const auth = authHeaders();
    if (!auth || starting) return;
    setStarting(true);
    setLoadError(null);
    try {
      await startDebate(auth, turnId);
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : "Failed to start the debate.");
    } finally {
      setStarting(false);
    }
  }

  async function handleCancel() {
    const auth = authHeaders();
    if (!auth || !session || cancelling) return;
    setCancelling(true);
    try {
      await cancelDebate(auth, turnId, session.id);
    } catch (error) {
      if (!(error instanceof ApiClientError && error.status === 409)) {
        setLoadError(error instanceof Error ? error.message : "Failed to cancel the debate.");
      }
    } finally {
      setCancelling(false);
    }
  }

  const selectedAnswer =
    modelAnswers.find((a) => a.model_id === selectedAnswerId) ?? modelAnswers[0];

  return (
    <Modal open={open} onClose={onClose} title="AI Debate" size="xl" bodyClassName="p-0">
      <div className="flex max-h-[80vh] flex-col">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border/60 px-5 py-4">
          <div className="min-w-0">
            <p className="text-sm font-semibold">AI Debate</p>
            {session ? (
              <p className="mt-0.5 flex items-center gap-2 text-xs text-muted-foreground">
                <span
                  className={cn(
                    "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium",
                    statusTone(session.status),
                  )}
                >
                  {active ? <Loader2 className="size-3 animate-spin" /> : null}
                  {STATUS_LABEL[session.status] ?? session.status}
                </span>
                <span>{progressSummary(session)}</span>
              </p>
            ) : loading ? (
              <p className="mt-0.5 text-xs text-muted-foreground">
                Checking for an existing debate…
              </p>
            ) : (
              <p className="mt-0.5 text-xs text-muted-foreground">
                No debate has been run for this answer yet.
              </p>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {session && active ? (
              <button
                type="button"
                onClick={handleCancel}
                disabled={cancelling}
                className="inline-flex items-center gap-1.5 rounded-lg border border-destructive/30 bg-destructive/5 px-2.5 py-1.5 text-xs font-medium text-destructive hover:bg-destructive/10 disabled:cursor-not-allowed disabled:opacity-60"
              >
                <Ban className="size-3.5" /> {cancelling ? "Stopping…" : "Stop Debate"}
              </button>
            ) : null}
            <button
              type="button"
              onClick={onClose}
              aria-label="Close debate panel"
              className="rounded-lg p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
            >
              <X className="size-4" />
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4">
          {loadError ? (
            <p className="mb-3 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
              {loadError}
            </p>
          ) : null}

          {!session && !loading ? (
            <div className="flex flex-col items-center gap-3 py-10 text-center">
              <MessagesSquare className="size-8 text-muted-foreground/60" />
              <p className="max-w-sm text-sm text-muted-foreground">
                Run an AI peer-review debate: every model reviews the others' answers, then responds
                to any challenges raised against its own.
              </p>
              <button
                type="button"
                onClick={handleStart}
                disabled={starting}
                className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground shadow-sm hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {starting ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <MessagesSquare className="size-4" />
                )}
                {starting ? "Starting…" : "Start Debate"}
              </button>
            </div>
          ) : session ? (
            <Tabs defaultValue="answers" className="w-full">
              <TabsList className="mb-4">
                <TabsTrigger value="answers">Original AI Answers</TabsTrigger>
                <TabsTrigger value="debate">AI Debate</TabsTrigger>
                <TabsTrigger value="findings">Debate Findings</TabsTrigger>
              </TabsList>

              <TabsContent value="answers" className="space-y-3">
                <div className="flex flex-wrap gap-2">
                  {modelAnswers.map((answer) => {
                    const m = modelById(answer.model_id);
                    return (
                      <button
                        key={answer.model_id}
                        type="button"
                        onClick={() => setSelectedAnswerId(answer.model_id)}
                        className={cn(
                          "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium transition",
                          selectedAnswerId === answer.model_id
                            ? "border-primary/40 bg-primary/10 text-primary"
                            : "border-border/60 bg-card text-muted-foreground hover:bg-accent",
                        )}
                      >
                        <VendorLogo vendor={m.vendor} className="size-3.5" title={m.name} />
                        {m.name}
                      </button>
                    );
                  })}
                </div>
                {selectedAnswer ? (
                  <div className="rounded-xl border border-border/70 bg-card/60 p-4">
                    <MessageContent>{selectedAnswer.text ?? ""}</MessageContent>
                  </div>
                ) : null}
              </TabsContent>

              <TabsContent value="debate" className="space-y-4">
                {rows.length === 0 ? (
                  <p className="rounded-lg border border-border/60 bg-muted/20 px-3 py-4 text-sm text-muted-foreground">
                    {active
                      ? "Waiting for the panel's peer review to complete…"
                      : "No challenges were raised in this debate."}
                  </p>
                ) : (
                  Array.from(rowsByTarget.entries()).map(([targetModelId, targetRows]) => {
                    const target = modelById(targetModelId);
                    return (
                      <div key={targetModelId} className="space-y-2">
                        <p className="flex items-center gap-1.5 text-xs font-semibold text-foreground/80">
                          <VendorLogo
                            vendor={target.vendor}
                            className="size-4"
                            title={target.name}
                          />
                          Challenges against {target.name}
                        </p>
                        <Accordion type="multiple" className="space-y-2">
                          {targetRows.map((row) => (
                            <ChallengeCard key={row.key} row={row} modelById={modelById} />
                          ))}
                        </Accordion>
                      </div>
                    );
                  })
                )}
              </TabsContent>

              <TabsContent value="findings" className="space-y-4">
                <FindingsSection session={session} modelById={modelById} />
                <UsageSummary session={session} />
              </TabsContent>
            </Tabs>
          ) : null}
        </div>

        {session && !active && session.status !== "completed" ? (
          <div className="border-t border-border/60 px-5 py-3">
            <RetryFooter turnId={turnId} onStart={handleStart} starting={starting} />
          </div>
        ) : null}
      </div>
    </Modal>
  );
}

function RetryFooter({
  turnId,
  onStart,
  starting,
}: {
  turnId: string;
  onStart: () => void;
  starting: boolean;
}) {
  void turnId;
  return (
    <div className="flex items-center justify-between gap-3">
      <p className="text-xs text-muted-foreground">
        Up to {DEBATE_MAX_ATTEMPTS_PER_TURN} debate attempts are allowed per answer. Paid model
        calls are never retried automatically.
      </p>
      <button
        type="button"
        onClick={onStart}
        disabled={starting}
        className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-primary/40 bg-primary/5 px-3 py-1.5 text-xs font-semibold text-primary hover:bg-primary/10 disabled:cursor-not-allowed disabled:opacity-60"
      >
        {starting ? (
          <Loader2 className="size-3.5 animate-spin" />
        ) : (
          <MessagesSquare className="size-3.5" />
        )}
        {starting ? "Starting…" : "Try Debate Again"}
      </button>
    </div>
  );
}
