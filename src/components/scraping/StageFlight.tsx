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
  pending: "border-white/15 bg-white/5 text-white/55",
  active: "border-sky-300/70 bg-sky-400/15 text-sky-100 dream-beacon",
  done: "border-teal-300/40 bg-teal-400/10 text-teal-50",
  failed: "border-rose-400/50 bg-rose-500/10 text-rose-100",
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
    <section className="relative overflow-hidden rounded-[1.75rem] border border-white/10 text-white shadow-[0_30px_80px_rgba(0,0,0,0.35)]">
      <div
        className="absolute inset-0"
        style={{
          background:
            "radial-gradient(90% 120% at 20% 0%, #245863 0%, #102229 45%, #0a1218 100%)",
        }}
      />
      <div
        className="dream-drift absolute -right-10 top-0 h-56 w-56 rounded-full blur-3xl"
        style={{ background: "rgba(56,189,248,0.18)" }}
      />

      <div className="relative px-5 pb-6 pt-5 sm:px-8 sm:pt-7">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="dream-rise">
            <p className="text-[11px] font-semibold uppercase tracking-[0.32em] text-sky-300/90">
              Scraping Council · Flight
            </p>
            <h2 className="mt-1 font-display text-2xl tracking-tight text-white sm:text-3xl">
              {countryName ? `Navigating ${countryName}` : "In flight"}
            </h2>
            <p className="mt-1 text-sm text-white/60">
              Stages move as sources open, evidence lands, and facilities crystallize.
            </p>
          </div>
          <div className="dream-rise dream-rise-delay-1 flex items-center gap-2 text-xs">
            <span className="rounded-full border border-white/15 bg-white/5 px-3 py-1 text-white/80">
              {statusLabel}
            </span>
            <span
              className={cn(
                "inline-flex items-center gap-1.5 rounded-full border px-3 py-1",
                connectionState === "Live"
                  ? "border-teal-300/40 bg-teal-400/10 text-teal-100"
                  : "border-white/15 bg-white/5 text-white/60",
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
        </div>

        {/* flight path */}
        <div className="relative mt-8 hidden h-28 md:block">
          <svg className="absolute inset-0 h-full w-full" viewBox="0 0 1000 120" fill="none">
            <path
              d="M40 80 C 220 20, 380 120, 500 55 S 780 10, 960 70"
              stroke="rgba(243,230,196,0.18)"
              strokeWidth="2"
            />
            <path
              className="dream-flight-path"
              d="M40 80 C 220 20, 380 120, 500 55 S 780 10, 960 70"
              stroke="rgba(56,189,248,0.75)"
              strokeWidth="2.5"
            />
            {/* moving vessel */}
            <circle
              r="7"
              fill="#38bdf8"
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
                  <span className="dream-pulse-ring absolute left-1/2 top-1/2 size-24 -translate-x-1/2 -translate-y-1/2 rounded-full border border-sky-300/40" />
                )}
                <div className="relative flex items-start justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className="flex size-8 items-center justify-center rounded-full border border-current/20 bg-black/20">
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
                  <p className="relative mt-3 text-[11px] uppercase tracking-[0.18em] text-sky-300">
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
