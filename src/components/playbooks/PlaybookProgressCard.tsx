import { AlertTriangle, Loader2 } from "lucide-react";
import { GlassCard } from "@/components/cinematic/PageChrome";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import type { ApiPlaybookRun } from "@/lib/api/types";
import {
  PLAYBOOK_FINALIZING_COPY,
  isPlaybookFinalizing,
  playbookProgressPercent,
  playbookRunStatusLabel,
  playbookWarningCopy,
} from "@/lib/playbooks";

export function PlaybookProgressCard({
  run,
  pollError,
}: {
  run: ApiPlaybookRun;
  pollError?: string | null;
}) {
  const queued = run.status === "queued";
  const processing = run.status === "processing";
  const finalizing = isPlaybookFinalizing(run);
  const percent = playbookProgressPercent(run.processed_count, run.total_count);
  const warning = playbookWarningCopy(run);
  const title = queued ? "Your Playbook is queued" : "Building your Playbook";
  const progressLabel =
    run.total_count > 0
      ? `${run.processed_count} of ${run.total_count} sources processed`
      : "Waiting for source counts";

  return (
    <GlassCard className="p-6 md:p-8" aria-live="polite">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-display text-2xl font-semibold tracking-tight">{title}</h2>
          <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
            {queued
              ? "Processing will begin through the Playbook worker. This page updates automatically."
              : finalizing
                ? PLAYBOOK_FINALIZING_COPY
                : "Eligible chats and Brain sources are being analyzed."}
          </p>
        </div>
        <Badge variant="secondary" className="capitalize">
          {playbookRunStatusLabel(run.status)}
        </Badge>
      </div>

      <div className="mt-6 flex items-center gap-3 text-sm">
        <Loader2 className="size-4 shrink-0 animate-spin text-primary" aria-hidden />
        <p>
          <span className="font-medium text-foreground">{progressLabel}</span>
          {run.total_count > 0 ? (
            <span className="text-muted-foreground"> ({Math.round(percent)}%)</span>
          ) : null}
        </p>
      </div>

      {processing && run.total_count > 0 ? (
        <Progress
          className="mt-4"
          value={percent}
          aria-label={progressLabel}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(percent)}
        />
      ) : null}

      {warning ? (
        <p className="mt-4 flex items-start gap-2 text-sm text-amber-800">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
          <span>
            {warning}. A usable Playbook can still be created from the sources that succeeded.
          </span>
        </p>
      ) : null}

      {pollError ? (
        <p className="mt-3 text-sm text-muted-foreground" role="status">
          {pollError}
        </p>
      ) : null}
    </GlassCard>
  );
}
