import { Info } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { parseCriteriaLines } from "@/lib/assessmentCriteria";
import type { Strategy } from "@/lib/mock";

type Props = {
  confidence: number;
  isTopPick: boolean;
  strategy: Strategy;
  criteria: string;
  modelName: string;
};

export function ModelConfidenceBadge({
  confidence,
  isTopPick,
  strategy,
  criteria,
  modelName,
}: Props) {
  const criteriaLines = parseCriteriaLines(criteria);

  return (
    <div className="ml-auto flex items-center gap-1">
      <span className="text-xs text-muted-foreground">{confidence}%</span>
      <Popover>
        <PopoverTrigger asChild>
          <button
            type="button"
            className="rounded p-0.5 text-muted-foreground hover:bg-accent hover:text-foreground"
            aria-label={`Why ${modelName} scored ${confidence}%`}
          >
            <Info className="size-3.5" />
          </button>
        </PopoverTrigger>
        <PopoverContent align="end" className="w-80 text-sm">
          <p className="font-medium text-foreground">Why {confidence}%?</p>
          <p className="mt-2 text-muted-foreground">
            <strong className="text-foreground">{modelName}</strong> self-rated its answer after
            applying your assessment criteria. Higher means it believes the answer better satisfies
            those priorities.
          </p>
          {criteriaLines.length > 0 ? (
            <ul className="mt-3 list-disc space-y-1 pl-4 text-muted-foreground">
              {criteriaLines.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          ) : (
            <p className="mt-3 text-muted-foreground">
              No custom criteria yet — scores reflect the model&apos;s general confidence in
              correctness. Set criteria from the chat header to steer scoring.
            </p>
          )}
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
