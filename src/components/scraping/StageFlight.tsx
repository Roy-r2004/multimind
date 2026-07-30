import { Compass, Link2, Radar, Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";

export type FlightStageId = "discover" | "verify" | "cite" | "clean";

export type FlightStage = {
  id: FlightStageId;
  label: string;
  hint: string;
  value: string;
  state: "pending" | "active" | "done" | "failed";
};

type Props = {
  stages: FlightStage[];
  statusLabel: string;
  connectionState: string;
  countryName?: string;
};

const ICONS = {
  discover: Radar,
  verify: Compass,
  cite: Link2,
  clean: Sparkles,
} as const;

const STATE_RING: Record<FlightStage["state"], string> = {
  pending: "border-slate-200/80 bg-white/55 text-slate-600",
  active: "border-sky-300/80 bg-sky-50/90 text-sky-900 dream-beacon",
  done: "border-teal-300/70 bg-teal-50/90 text-teal-900",
  failed: "border-rose-300/70 bg-rose-50/90 text-rose-900",
};

export function StageFlight({ stages, statusLabel, connectionState, countryName }: Props) {
  const activeIndex = Math.max(
    0,
    stages.findIndex((s) => s.state === "active"),
  );
  const progress =
    stages.length <= 1
      ? 0
      : (stages.filter((s) => s.state === "done").length +
          (stages.some((s) => s.state === "active") ? 0.45 : 0)) /
        (stages.length - 0.001);

  return (
    <section className="relative overflow-hidden rounded-[1.75rem] border border-white/70 bg-white/55 text-slate-900 shadow-[0_24px_60px_rgba(22,48,58,0.08)] backdrop-blur-xl">
      <div
        className="absolute inset-0"
        style={{
          background:
            "radial-gradient(90% 120% at 12% 0%, rgba(125,211,252,0.35) 0%, transparent 48%), radial-gradient(80% 100% at 90% 10%, rgba(167,243,208,0.28) 0%, transparent 45%), linear-gradient(180deg, rgba(255,255,255,0.72), rgba(248,250,252,0.55))",
        }}
      />
      <div
        className="dream-drift absolute -right-10 top-0 h-56 w-56 rounded-full blur-3xl"
        style={{ background: "rgba(56,189,248,0.22)" }}
      />
      <div
        className="dream-drift-alt absolute -left-8 bottom-0 h-44 w-44 rounded-full blur-3xl"
        style={{ background: "rgba(52,211,153,0.18)" }}
      />

      <div className="relative px-5 pb-6 pt-5 sm:px-8 sm:pt-7">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="dream-rise">
            <p className="text-[11px] font-semibold uppercase tracking-[0.32em] text-sky-700/90">
              Scraping Council · Flight
            </p>
            <h2 className="mt-1 font-display text-2xl tracking-tight text-slate-900 sm:text-3xl">
              {countryName ? `Navigating ${countryName}` : "In flight"}
            </h2>
            <p className="mt-1 text-sm text-slate-600">
              Stages move as sources open, evidence lands, and facilities crystallize.
            </p>
          </div>
          <div className="dream-rise dream-rise-delay-1 flex items-center gap-2 text-xs">
            <span className="rounded-full border border-slate-200 bg-white/80 px-3 py-1 text-slate-700">
              {statusLabel}
            </span>
            <span
              className={cn(
                "inline-flex items-center gap-1.5 rounded-full border px-3 py-1",
                connectionState === "Live"
                  ? "border-teal-300/70 bg-teal-50 text-teal-800"
                  : "border-slate-200 bg-white/70 text-slate-600",
              )}
            >
              <span
                className={cn(
                  "size-1.5 rounded-full",
                  connectionState === "Live" ? "bg-teal-500 dream-twinkle" : "bg-slate-400",
                )}
              />
              {connectionState}
            </span>
          </div>
        </div>

        <div className="relative mt-8 hidden h-28 md:block">
          <svg className="absolute inset-0 h-full w-full" viewBox="0 0 1000 120" fill="none">
            <path
              d="M40 80 C 220 20, 380 120, 500 55 S 780 10, 960 70"
              stroke="rgba(14,116,144,0.12)"
              strokeWidth="2"
            />
            <path
              className="dream-flight-path"
              d="M40 80 C 220 20, 380 120, 500 55 S 780 10, 960 70"
              stroke="rgba(14,116,144,0.75)"
              strokeWidth="2.5"
            />
            <circle
              r="8"
              fill="oklch(0.62 0.14 210)"
              className="dream-float"
              cx={40 + progress * 920}
              cy={80 - Math.sin(progress * Math.PI) * 35}
            >
              <animate
                attributeName="opacity"
                values="0.7;1;0.7"
                dur="2s"
                repeatCount="indefinite"
              />
            </circle>
          </svg>
        </div>

        <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {stages.map((stage, index) => {
            const Icon = ICONS[stage.id];
            return (
              <div
                key={stage.id}
                className={cn(
                  "dream-rise relative rounded-2xl border px-4 py-4 backdrop-blur-sm transition-transform duration-500",
                  STATE_RING[stage.state],
                  `dream-rise-delay-${index + 1}`,
                  stage.state === "active" && "dream-float -translate-y-1",
                )}
              >
                {stage.state === "active" && (
                  <span className="dream-pulse-ring absolute left-1/2 top-1/2 size-24 -translate-x-1/2 -translate-y-1/2 rounded-full border border-sky-400/40" />
                )}
                <div className="relative flex items-start justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className="flex size-8 items-center justify-center rounded-full border border-current/15 bg-white/70">
                      <Icon className="size-3.5" />
                    </span>
                    <div>
                      <p className="text-[10px] uppercase tracking-[0.2em] opacity-70">
                        Stage {index + 1}
                      </p>
                      <p className="font-medium tracking-tight">{stage.label}</p>
                    </div>
                  </div>
                  <span className="text-[10px] uppercase tracking-wide opacity-70">
                    {stage.state}
                  </span>
                </div>
                <p className="relative mt-3 text-2xl font-semibold tabular-nums tracking-tight">
                  {stage.value}
                </p>
                <p className="relative mt-1 text-xs opacity-70">{stage.hint}</p>
                {index === activeIndex && stage.state === "active" && (
                  <p className="relative mt-3 text-[11px] uppercase tracking-[0.18em] text-sky-700">
                    Vessel is here
                  </p>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}

/** Map live execution counters into the four dreamflight stages. */
export function buildFlightStages(input: {
  sources: number;
  pages: number;
  facilities: number;
  duplicates: number;
  status: string;
  stageStates?: Partial<Record<FlightStageId, FlightStage["state"]>>;
}): FlightStage[] {
  const running = !["completed", "failed", "cancelled"].includes(input.status);
  const discoverDone = input.sources > 0 && input.pages > 0;
  const verifyActive = running && input.pages > 0 && input.facilities === 0;
  const citeActive = running && input.facilities > 0;
  const cleanDone = !running && input.facilities >= 0;

  return [
    {
      id: "discover",
      label: "Discover",
      hint: "Registries, directories, multilingual search",
      value: String(input.sources),
      state: input.stageStates?.discover ?? (discoverDone ? "done" : running ? "active" : "pending"),
    },
    {
      id: "verify",
      label: "Verify",
      hint: "Location · phone · country gates",
      value: String(input.pages),
      state:
        input.stageStates?.verify ??
        (input.facilities > 0 || (!running && input.pages > 0)
          ? "done"
          : verifyActive
            ? "active"
            : discoverDone
              ? "pending"
              : "pending"),
    },
    {
      id: "cite",
      label: "Cite",
      hint: "Evidence quotes · branch safety",
      value: String(input.facilities),
      state:
        input.stageStates?.cite ??
        (!running && input.facilities > 0 ? "done" : citeActive ? "active" : "pending"),
    },
    {
      id: "clean",
      label: "Clean",
      hint: "Normalize · dedupe · publish",
      value: String(input.duplicates),
      state:
        input.stageStates?.clean ??
        (cleanDone && !running ? "done" : running && input.facilities > 0 ? "active" : "pending"),
    },
  ];
}
