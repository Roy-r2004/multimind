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
  pending: "border-white/10 bg-white/[0.03] text-white/50",
  active: "border-[#d4a84b]/50 bg-[#d4a84b]/12 text-[#f3e6c4] dream-beacon dream-float",
  done: "border-teal-300/30 bg-teal-400/10 text-teal-50",
  failed: "border-rose-400/40 bg-rose-500/10 text-rose-100",
};

export function RunPulse({ stages, connectionState, statusLabel, onStageClick }: Props) {
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge
          variant="secondary"
          className="border border-white/15 bg-white/5 text-[#f7f1e4]"
        >
          {statusLabel}
        </Badge>
        <span
          className={cn(
            "inline-flex items-center gap-1.5 text-sm",
            connectionState === "Live" ? "text-teal-200" : "text-white/50",
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
              "rounded-xl border px-3 py-3 text-left transition hover:border-[#d4a84b]/35",
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
