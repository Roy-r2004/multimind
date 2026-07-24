import { Badge } from "@/components/ui/badge";

export type RunPulseStageId = "sources" | "pages" | "facilities" | "confidence";

type Stage = {
  id: RunPulseStageId;
  label: string;
  value: string;
  state: "pending" | "active" | "done" | "failed";
};

type Props = {
  stages: Stage[];
  connectionState: string;
  statusLabel: string;
  onStageClick?: (id: RunPulseStageId) => void;
};

const stateClass: Record<Stage["state"], string> = {
  pending: "border-border/70 text-muted-foreground",
  active: "border-primary/50 bg-primary/5 text-foreground",
  done: "border-border bg-muted/30 text-foreground",
  failed: "border-destructive/40 bg-destructive/5 text-destructive",
};

export function RunPulse({ stages, connectionState, statusLabel, onStageClick }: Props) {
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="secondary">{statusLabel}</Badge>
        <span className="text-sm text-muted-foreground">{connectionState}</span>
      </div>
      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        {stages.map((stage, index) => (
          <button
            key={stage.id}
            type="button"
            onClick={() => onStageClick?.(stage.id)}
            className={`rounded-xl border px-3 py-3 text-left transition-colors hover:bg-muted/40 ${stateClass[stage.state]}`}
          >
            <div className="flex items-center justify-between gap-2">
              <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
                {index + 1}. {stage.label}
              </p>
              <span className="text-[10px] uppercase tracking-wide opacity-70">{stage.state}</span>
            </div>
            <p className="mt-1.5 text-xl font-semibold tabular-nums">{stage.value}</p>
          </button>
        ))}
      </div>
    </div>
  );
}
