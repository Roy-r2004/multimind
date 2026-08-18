import { useState } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  AlertTriangle,
  CalendarClock,
  ChevronDown,
  Eye,
  GitBranch,
  Sparkles,
  type LucideIcon,
} from "lucide-react";
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
  const [expanded, setExpanded] = useState(false);
  const canCollapse = summary.length > 1_100 || summary.split("\n").length > 18;

  return (
    <GlassCard className="overflow-hidden p-0">
      <div className="border-b border-border/70 bg-gradient-to-r from-primary/[0.07] via-primary/[0.025] to-transparent px-6 py-6 md:px-8 md:py-7">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex items-start gap-3">
            <span className="mt-0.5 grid size-10 shrink-0 place-items-center rounded-xl border border-primary/15 bg-primary/10 text-primary">
              <Sparkles className="size-5" aria-hidden />
            </span>
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-primary">
                Overview
              </p>
              <h2 className="mt-1 font-display text-2xl font-semibold tracking-tight">
                Your Playbook
              </h2>
              <p className="mt-1.5 text-sm text-muted-foreground">{PLAYBOOK_SAVED_COPY}</p>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge variant="secondary" className="rounded-full px-3 py-1">
              Version {playbook.playbook_version}
            </Badge>
            {successfulRun ? (
              <Badge
                variant="outline"
                className="rounded-full border-emerald-200 bg-emerald-50 px-3 py-1 text-emerald-800"
              >
                {playbookRunStatusLabel(run.status)}
              </Badge>
            ) : null}
          </div>
        </div>
      </div>

      <div className="px-6 py-7 md:px-8 md:py-9">
        <div className="mx-auto max-w-4xl">
          <div className="relative">
            <div
              id="playbook-summary-content"
              className={canCollapse && !expanded ? "max-h-[34rem] overflow-hidden" : undefined}
            >
              {playbook.core_summary?.trim() ? (
                <ReactMarkdown remarkPlugins={[remarkGfm]} components={summaryComponents}>
                  {summary}
                </ReactMarkdown>
              ) : (
                <p className="text-sm leading-7 text-muted-foreground">{PLAYBOOK_EMPTY_SUMMARY}</p>
              )}
            </div>
            {canCollapse && !expanded ? (
              <div className="pointer-events-none absolute inset-x-0 bottom-0 h-28 bg-gradient-to-t from-card via-card/95 to-transparent" />
            ) : null}
          </div>
          {canCollapse ? (
            <div className="mt-4 flex justify-center border-t border-border/60 pt-4">
              <button
                type="button"
                className="inline-flex items-center gap-1.5 rounded-full px-4 py-2 text-sm font-medium text-primary transition-colors hover:bg-primary/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                aria-expanded={expanded}
                aria-controls="playbook-summary-content"
                onClick={() => setExpanded((value) => !value)}
              >
                {expanded ? "Show less" : "Show full playbook"}
                <ChevronDown
                  className={`size-4 transition-transform ${expanded ? "rotate-180" : ""}`}
                  aria-hidden
                />
              </button>
            </div>
          ) : null}
        </div>
      </div>

      <dl className="grid border-y border-border/70 bg-muted/25 sm:grid-cols-2 lg:grid-cols-3">
        <Meta icon={CalendarClock} label="Last generated" value={generatedAt || "Not recorded"} />
        <Meta icon={Eye} label="Observations" value={`${observationCount} loaded`} />
        {successfulRun ? (
          <Meta icon={GitBranch} label="Latest run" value={playbookRunStatusLabel(run.status)} />
        ) : null}
      </dl>

      <div className="px-6 py-5 md:px-8">
        {warning ? (
          <p className="mb-4 flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
            <span>{warning}. The saved Playbook remains usable.</span>
          </p>
        ) : null}
        <p className="text-xs leading-relaxed text-muted-foreground">{PLAYBOOK_RERUN_NOTE}</p>
      </div>
    </GlassCard>
  );
}

const summaryComponents: Components = {
  h1: ({ children }) => (
    <h3 className="mb-3 mt-8 font-display text-xl font-semibold tracking-tight first:mt-0">
      {children}
    </h3>
  ),
  h2: ({ children }) => (
    <h3 className="mb-3 mt-8 border-b border-border/60 pb-2 font-display text-lg font-semibold tracking-tight first:mt-0">
      {children}
    </h3>
  ),
  h3: ({ children }) => (
    <h4 className="mb-2 mt-6 text-base font-semibold first:mt-0">{children}</h4>
  ),
  p: ({ children }) => (
    <p className="my-3 text-[15px] leading-7 text-foreground/90 first:mt-0 last:mb-0">{children}</p>
  ),
  ul: ({ children }) => (
    <ul className="my-4 list-disc space-y-2 pl-5 text-[15px] leading-7 marker:text-primary">
      {children}
    </ul>
  ),
  ol: ({ children }) => (
    <ol className="my-4 list-decimal space-y-2 pl-5 text-[15px] leading-7 marker:font-semibold marker:text-primary">
      {children}
    </ol>
  ),
  li: ({ children }) => <li className="pl-1">{children}</li>,
  strong: ({ children }) => <strong className="font-semibold text-foreground">{children}</strong>,
  blockquote: ({ children }) => (
    <blockquote className="my-5 rounded-r-lg border-l-2 border-primary/50 bg-primary/[0.04] px-4 py-2 text-muted-foreground">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="my-7 border-border/70" />,
  a: ({ children, href }) => (
    <a
      href={href}
      className="font-medium text-primary underline underline-offset-4"
      target="_blank"
      rel="noreferrer"
    >
      {children}
    </a>
  ),
};

function Meta({ icon: Icon, label, value }: { icon: LucideIcon; label: string; value: string }) {
  return (
    <div className="flex items-center gap-3 border-b border-border/60 px-6 py-4 last:border-b-0 sm:border-b-0 sm:border-r sm:last:border-r-0 md:px-8">
      <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-background text-muted-foreground shadow-sm ring-1 ring-border/70">
        <Icon className="size-4" aria-hidden />
      </span>
      <div>
        <dt className="text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
          {label}
        </dt>
        <dd className="mt-0.5 text-sm font-medium text-foreground">{value}</dd>
      </div>
    </div>
  );
}
