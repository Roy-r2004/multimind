import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

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
  pending: "border-border bg-muted/30 text-muted-foreground",
  active: "border-primary/50 bg-primary/10 text-primary dream-beacon dream-float",
  done: "border-teal-300/40 bg-teal-50 text-teal-800",
  failed: "border-rose-300/50 bg-rose-50 text-rose-700",
};

export function RunPulse({ stages, connectionState, statusLabel, onStageClick }: Props) {
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge
          variant="secondary"
          className="border border-border bg-muted/40 text-foreground"
        >
          {statusLabel}
        </Badge>
        <span
          className={cn(
            "inline-flex items-center gap-1.5 text-sm",
            connectionState === "Live" ? "text-teal-700" : "text-muted-foreground",
          )}
        >
          <span
            className={cn(
              "size-1.5 rounded-full",
              connectionState === "Live" ? "bg-teal-300 dream-twinkle" : "bg-white/40",
            )}
          />
          {connectionState}
        </span>
      </div>
      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        {stages.map((stage, index) => (
          <button
            key={stage.id}
            type="button"
            onClick={() => onStageClick?.(stage.id)}
            className={cn(
              "rounded-xl border px-3 py-3 text-left transition hover:border-primary/35",
              stateClass[stage.state],
            )}
          >
            <div className="flex items-center justify-between gap-2">
              <p className="text-[11px] uppercase tracking-[0.14em] opacity-70">
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
