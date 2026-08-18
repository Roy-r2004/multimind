import { Badge } from "@/components/ui/badge";
import { GlassCard } from "@/components/cinematic/PageChrome";
import type { ApiPlaybookObservation } from "@/lib/api/types";
import {
  formatPlaybookTimestamp,
  observationConfidenceLabel,
  observationStatusLabel,
} from "@/lib/playbooks";
import { cn } from "@/lib/utils";

const STATUS_CLASS: Record<string, string> = {
  rejected: "border-rose-200 bg-rose-50 text-rose-800",
  superseded: "border-amber-200 bg-amber-50 text-amber-900",
  uncertain: "border-border bg-muted text-muted-foreground",
  confirmed: "border-teal-200 bg-teal-50 text-teal-800",
  completed: "border-teal-200 bg-teal-50 text-teal-800",
};

export function PlaybookObservationCard({ observation }: { observation: ApiPlaybookObservation }) {
  const confidence = observationConfidenceLabel(observation.confidence);
  const firstSeen = formatPlaybookTimestamp(observation.first_observed_at);
  const lastConfirmed = formatPlaybookTimestamp(observation.last_confirmed_at);

  return (
    <GlassCard className="p-4 md:p-5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h3 className="text-base font-semibold leading-snug">
          {observation.subject?.trim() || "Observation"}
        </h3>
        <div className="flex flex-wrap gap-1.5">
          <Badge variant="outline" className={cn("font-medium", STATUS_CLASS[observation.status])}>
            {observationStatusLabel(observation.status)}
          </Badge>
          {confidence ? (
            <Badge variant="secondary" className="font-normal">
              {confidence}
            </Badge>
          ) : null}
        </div>
      </div>
      <p className="mt-3 whitespace-pre-wrap text-sm leading-relaxed text-foreground">
        {observation.observation}
      </p>
      <dl className="mt-4 flex flex-col gap-1 text-xs text-muted-foreground sm:flex-row sm:flex-wrap sm:gap-x-4">
        <Meta
          label="Evidence"
          value={
            observation.evidence_count === 1 ? "1 source" : `${observation.evidence_count} sources`
          }
        />
        {firstSeen ? <Meta label="First observed" value={firstSeen} /> : null}
        {lastConfirmed ? <Meta label="Last confirmed" value={lastConfirmed} /> : null}
      </dl>
    </GlassCard>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="inline">{label}: </dt>
      <dd className="inline">{value}</dd>
    </div>
  );
}
