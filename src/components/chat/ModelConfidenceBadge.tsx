import { Info } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import type { Strategy } from "@/lib/mock";

type Props = {
  confidence: number;
  isTopPick: boolean;
  strategy: Strategy;
  modelName: string;
};

export function ModelConfidenceBadge({
  confidence,
  isTopPick,
  strategy,
  modelName,
}: Props) {
  return (
    <div className="ml-auto flex items-center gap-1">
      <span className="text-xs font-semibold tabular-nums text-slate-200">{confidence}%</span>
      <Popover>
        <PopoverTrigger asChild>
          <button
            type="button"
            className="rounded p-0.5 text-slate-400 hover:bg-white/10 hover:text-white"
            aria-label={`Why ${modelName} scored ${confidence}%`}
          >
            <Info className="size-3.5" />
          </button>
        </PopoverTrigger>
        <PopoverContent align="end" className="w-80 text-sm">
          <p className="font-medium text-foreground">Why {confidence}%?</p>
          <p className="mt-2 text-muted-foreground">
            <strong className="text-foreground">{modelName}</strong> self-rated confidence that its
            answer is correct and useful for the current question.
          </p>
          {isTopPick && (
            <p className="mt-3 rounded-lg bg-amber-500/10 px-2.5 py-2 text-xs text-amber-900 dark:text-amber-200">
              {strategy === "Pick Best"
                ? "Top pick — the Verdict AI named this model as the strongest answer for this question."
                : "Top pick — highest confidence in the council for this turn (Verdict may still blend all answers)."}
            </p>
          )}
        </PopoverContent>
      </Popover>
    </div>
  );
}
