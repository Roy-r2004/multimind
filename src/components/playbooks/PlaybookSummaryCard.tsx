import { AlertTriangle } from "lucide-react";
import { GlassCard } from "@/components/cinematic/PageChrome";
import { Badge } from "@/components/ui/badge";
import type { ApiPlaybook, ApiPlaybookRun } from "@/lib/api/types";
import {
  PLAYBOOK_EMPTY_SUMMARY,
  PLAYBOOK_RERUN_NOTE,
  PLAYBOOK_SAVED_COPY,
  formatPlaybookTimestamp,
  playbookCoreSummaryText,
  playbookRunStatusLabel,
  playbookWarningCopy,
} from "@/lib/playbooks";

export function PlaybookSummaryCard({
  playbook,
  run,
  observationCount,
}: {
  playbook: ApiPlaybook;
  run: ApiPlaybookRun | null;
  observationCount: number;
}) {
  const summary = playbookCoreSummaryText(playbook.core_summary);
  const warning = playbookWarningCopy(run);
  const generatedAt = formatPlaybookTimestamp(playbook.last_success_at);
  const successfulRun =
    run && (run.status === "completed" || run.status === "completed_with_warnings");

  return (
    <GlassCard className="p-6 md:p-8">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-display text-2xl font-semibold tracking-tight">Your Playbook</h2>
          <p className="mt-2 text-sm text-muted-foreground">{PLAYBOOK_SAVED_COPY}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Badge variant="secondary">Version {playbook.playbook_version}</Badge>
          {successfulRun ? (
            <Badge variant="outline">{playbookRunStatusLabel(run.status)}</Badge>
          ) : null}
        </div>
      </div>

      <div className="mt-6 whitespace-pre-wrap text-sm leading-relaxed text-foreground">
        {playbook.core_summary?.trim() ? summary : PLAYBOOK_EMPTY_SUMMARY}
      </div>

      <dl className="mt-6 grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-3">
        <Meta label="Last generated" value={generatedAt || "Not recorded"} />
        <Meta label="Observations" value={`${observationCount} loaded`} />
        {successfulRun ? (
          <Meta label="Latest run" value={playbookRunStatusLabel(run.status)} />
        ) : null}
      </dl>

      {warning ? (
        <p className="mt-5 flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
          <span>{warning}. The saved Playbook remains usable.</span>
        </p>
      ) : null}

      <p className="mt-5 text-xs text-muted-foreground">{PLAYBOOK_RERUN_NOTE}</p>
    </GlassCard>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-muted-foreground">{label}</dt>
      <dd className="mt-1 font-medium">{value}</dd>
    </div>
  );
}
