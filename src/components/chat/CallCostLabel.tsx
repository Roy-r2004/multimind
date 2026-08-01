import { Coins, Sparkles } from "lucide-react";

import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { calculateTurnCost, formatCostAmount } from "@/lib/cost";
import { cn } from "@/lib/utils";

type CallCostKind = "answer" | "verdict";

/** High-contrast metadata pill for a single OpenRouter call cost. */
export function CallCostLabel({
  cost,
  kind = "answer",
  className,
}: {
  cost: number | null | undefined;
  kind?: CallCostKind;
  className?: string;
}) {
  const amount = formatCostAmount(cost);
  if (!amount) return null;

  const isVerdict = kind === "verdict";
  const tooltip = isVerdict
    ? "Cost of generating this Verdict"
    : "Cost of this AI response";
  const label = isVerdict ? "Verdict cost" : "Cost";

  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            className={cn(
              "inline-flex max-w-full shrink-0 items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-semibold tabular-nums shadow-sm ring-1",
              isVerdict
                ? "border-sky-600/25 bg-sky-500/15 text-sky-950 ring-sky-500/15 dark:text-sky-50"
                : "border-teal-600/30 bg-teal-500/15 text-teal-950 ring-teal-500/20 dark:text-teal-50",
              className,
            )}
            aria-label={`${tooltip}: ${amount}`}
          >
            <Sparkles
              className={cn(
                "size-3.5 shrink-0",
                isVerdict ? "text-sky-700 dark:text-sky-300" : "text-teal-700 dark:text-teal-300",
              )}
              aria-hidden
            />
            <span className="font-medium opacity-90">{label}</span>
            <span className="font-semibold tracking-tight">{amount}</span>
          </span>
        </TooltipTrigger>
        <TooltipContent side="top">{tooltip}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

/** Stronger turn-cost summary chip — visually above per-call pills, not a CTA. */
export function TurnCostSummary({
  answers,
  verdictCost,
  className,
}: {
  answers: Array<{ cost_usd?: number | null } | null | undefined>;
  verdictCost?: number | null;
  className?: string;
}) {
  const amount = formatCostAmount(calculateTurnCost(answers, verdictCost));
  if (!amount) return null;

  const tooltip = "Total cost of all AI responses and the Verdict in this turn";

  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            className={cn(
              "pointer-events-auto inline-flex max-w-full shrink-0 cursor-default items-center gap-2 rounded-full border border-emerald-700/30 bg-emerald-600/20 px-3 py-1.5 text-xs shadow-md ring-1 ring-emerald-500/25",
              className,
            )}
            aria-label={`${tooltip}: ${amount}`}
          >
            <Coins className="size-4 shrink-0 text-emerald-800 dark:text-emerald-300" aria-hidden />
            <span className="font-medium text-emerald-900/80 dark:text-emerald-100/80">
              Turn total
            </span>
            <span className="font-bold tabular-nums tracking-tight text-emerald-950 dark:text-emerald-50">
              {amount}
            </span>
          </span>
        </TooltipTrigger>
        <TooltipContent side="top">{tooltip}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

/** @deprecated Use TurnCostSummary */
export const TurnTotalCostBadge = TurnCostSummary;
