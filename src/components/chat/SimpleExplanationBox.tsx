import type { ReactNode } from "react";
import { Lightbulb, Loader2 } from "lucide-react";
import { GlassCard } from "@/components/cinematic/PageChrome";
import { MessageContent } from "@/components/chat/MessageContent";

const cardClassName =
  "border-emerald-200/80 bg-emerald-50/75 p-4 shadow-none dark:border-emerald-800/55 dark:bg-emerald-950/35 sm:p-5";
const headerClassName =
  "mb-3 flex items-center gap-2 border-b border-emerald-200/70 pb-2.5 dark:border-emerald-800/50";
const iconClassName =
  "grid size-7 shrink-0 place-items-center rounded-lg bg-emerald-100 text-emerald-700 dark:bg-emerald-900/70 dark:text-emerald-300";
const titleClassName = "text-sm font-semibold tracking-tight text-emerald-800 dark:text-emerald-200";

function SimpleExplanationHeader({ icon }: { icon: ReactNode }) {
  return (
    <div className={headerClassName}>
      <span className={iconClassName}>{icon}</span>
      <h4 className={titleClassName}>Simple Explanation</h4>
    </div>
  );
}

export function SimpleExplanationBox({ content }: { content: string }) {
  return (
    <div data-testid="simple-explanation">
      <GlassCard className={cardClassName}>
        <SimpleExplanationHeader icon={<Lightbulb className="size-3.5" />} />
        <MessageContent compact muted>
          {content}
        </MessageContent>
      </GlassCard>
    </div>
  );
}

export function SimpleExplanationLoadingBox() {
  return (
    <div data-testid="simple-explanation-loading">
      <GlassCard className={cardClassName}>
        <SimpleExplanationHeader icon={<Loader2 className="size-3.5 animate-spin" aria-hidden />} />
        <p className="text-sm font-medium text-emerald-900/85 dark:text-emerald-100/85">
          Making this easier to understand…
        </p>
        <p className="mt-1 text-xs text-emerald-800/70 dark:text-emerald-200/65">
          This usually takes a few seconds.
        </p>
        <div className="mt-3 space-y-2" aria-hidden>
          <div className="h-2 w-11/12 animate-pulse rounded bg-emerald-200/80 dark:bg-emerald-800/55" />
          <div className="h-2 w-9/12 animate-pulse rounded bg-emerald-200/65 dark:bg-emerald-800/45" />
          <div className="h-2 w-7/12 animate-pulse rounded bg-emerald-200/50 dark:bg-emerald-800/35" />
        </div>
      </GlassCard>
    </div>
  );
}
