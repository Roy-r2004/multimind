import { createFileRoute } from "@tanstack/react-router";
import { AppShell } from "@/components/AppShell";
import { ChevronDown, CircleDollarSign, Sparkles } from "lucide-react";

export const Route = createFileRoute("/costs")({
  head: () => ({ meta: [{ title: "Costs — MultiAI" }] }),
  component: CostsPage,
});

const budgetData = [
  { label: "GPT-4.1", value: 40, cost: "$5.10", color: "#14b8a6" },
  { label: "Claude 3.5", value: 31, cost: "$3.90", color: "#f59e0b" },
  { label: "Gemini 1.5", value: 14, cost: "$1.80", color: "#3b82f6" },
  { label: "DeepSeek V3", value: 5, cost: "$0.70", color: "#8b5cf6" },
  { label: "Other Models", value: 10, cost: "$1.25", color: "#94a3b8" },
];

function CostsPage() {
  return (
    <AppShell>
      <div className="mx-auto max-w-6xl px-6 py-10">
        <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Costs</h1>
            <p className="mt-1 text-sm text-muted-foreground">Track your AI usage and spending.</p>
          </div>
          <div className="inline-flex items-center gap-2 rounded-xl border border-border bg-card px-3 py-2 text-sm text-muted-foreground">
            <span>Last 30 Days</span>
            <ChevronDown className="size-4" />
          </div>
        </div>

        <div className="mt-8 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <StatCard
            title="Monthly Budget"
            amount="$12.75 / $50.00"
            body="25% used"
            footer="$37.25 remaining"
            progress={25}
          />
          <StatCard title="Today" amount="$0.42" body="12,340 tokens" />
          <StatCard title="This Week" amount="$3.18" body="85,200 tokens" />
          <StatCard title="This Month" amount="$12.75" body="341,000 tokens" />
        </div>

        <div className="mt-8 grid gap-4 lg:grid-cols-[1.1fr_0.9fr]">
          <div className="rounded-2xl border border-border bg-card p-6 sm:p-7">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h2 className="text-base font-semibold tracking-tight">Top Models by Cost</h2>
                <p className="mt-1 text-sm text-muted-foreground">Usage share by model</p>
              </div>
            </div>

            <div className="mt-8 flex flex-col gap-6 lg:flex-row lg:items-center lg:gap-8">
              <div className="mx-auto flex size-64 items-center justify-center">
                <div
                  className="relative flex size-56 items-center justify-center rounded-full"
                  style={{
                    background: `conic-gradient(#14b8a6 0 40%, #f59e0b 40% 71%, #3b82f6 71% 85%, #8b5cf6 85% 90%, #94a3b8 90% 100%)`,
                  }}
                >
                  <div className="absolute inset-4 rounded-full bg-background" />
                  <div className="relative text-center">
                    <div className="text-[10px] font-semibold uppercase tracking-[0.24em] text-muted-foreground">
                      Total
                    </div>
                    <div className="mt-1 text-2xl font-semibold tracking-tight text-foreground">
                      $12.75
                    </div>
                  </div>
                </div>
              </div>

              <div className="w-full max-w-sm">
                <div className="grid grid-cols-[1fr_auto_auto] gap-x-4 gap-y-2 text-sm text-muted-foreground">
                  {budgetData.map((item) => (
                    <div key={item.label} className="contents">
                      <div className="flex items-center gap-2">
                        <span
                          className="size-2.5 rounded-full"
                          style={{ backgroundColor: item.color }}
                        />
                        <span className="text-foreground">{item.label}</span>
                      </div>
                      <span className="text-right font-medium text-foreground">{item.cost}</span>
                      <span className="text-right">{item.value}%</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>

          <div className="rounded-2xl border border-border bg-card p-6 sm:p-7">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h2 className="text-base font-semibold tracking-tight">Usage & Cost Breakdown</h2>
                <p className="mt-1 text-sm text-muted-foreground">Snapshot for this period</p>
              </div>
              <div className="rounded-full bg-accent p-2 text-muted-foreground">
                <CircleDollarSign className="size-4" />
              </div>
            </div>

            <div className="mt-8 space-y-2.5">
              {[
                ["Total Tokens", "1,245,000"],
                ["Total Cost", "$12.75"],
                ["Most Used Model", "GPT-4.1"],
                ["Most Expensive Model", "Claude 3.5"],
                ["Average Cost Per Chat", "$0.009"],
              ].map(([label, value]) => (
                <div
                  key={label}
                  className="flex items-center justify-between rounded-xl border border-border bg-background/70 px-4 py-3.5"
                >
                  <span className="text-sm text-muted-foreground">{label}</span>
                  <span className="text-sm font-semibold text-foreground">{value}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </AppShell>
  );
}

function StatCard({
  title,
  amount,
  body,
  footer,
  progress,
}: {
  title: string;
  amount: string;
  body: string;
  footer?: string;
  progress?: number;
}) {
  return (
    <div className="rounded-2xl border border-border bg-card p-5 sm:p-6">
      <div className="flex items-center gap-2 text-sm font-medium text-foreground">
        <Sparkles className="size-4 text-primary" /> {title}
      </div>
      <div className="mt-5 text-3xl font-semibold tracking-tight text-foreground">{amount}</div>
      <div className="mt-2 text-sm text-muted-foreground">{body}</div>
      {progress !== undefined ? (
        <div className="mt-5">
          <div className="h-2 rounded-full bg-muted">
            <div className="h-2 rounded-full bg-primary" style={{ width: `${progress}%` }} />
          </div>
          <div className="mt-2 text-sm text-muted-foreground">{footer}</div>
        </div>
      ) : null}
    </div>
  );
}
