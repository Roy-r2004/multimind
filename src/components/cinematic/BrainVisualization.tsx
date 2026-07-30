import { cn } from "@/lib/utils";
import brainAnatomy from "@/assets/brain/realistic-neural-brain.png";

const NODES = [
  { cx: 22, cy: 44, r: 1.7, delay: 0 },
  { cx: 29, cy: 34, r: 1.2, delay: 0.35 },
  { cx: 39, cy: 27, r: 1.5, delay: 0.7 },
  { cx: 51, cy: 25, r: 1.3, delay: 1.05 },
  { cx: 63, cy: 29, r: 1.7, delay: 1.4 },
  { cx: 74, cy: 38, r: 1.3, delay: 1.75 },
  { cx: 79, cy: 51, r: 1.6, delay: 0.2 },
  { cx: 69, cy: 56, r: 1.2, delay: 0.55 },
  { cx: 57, cy: 49, r: 1.8, delay: 0.9 },
  { cx: 46, cy: 41, r: 1.2, delay: 1.25 },
  { cx: 35, cy: 49, r: 1.6, delay: 1.6 },
  { cx: 46, cy: 58, r: 1.4, delay: 1.95 },
  { cx: 58, cy: 64, r: 1.3, delay: 0.45 },
  { cx: 71, cy: 67, r: 1.7, delay: 0.8 },
  { cx: 55, cy: 75, r: 1.2, delay: 1.15 },
];

const SYNAPSES: Array<[number, number, number, number]> = [
  [22, 44, 29, 34],
  [22, 44, 35, 49],
  [29, 34, 39, 27],
  [29, 34, 46, 41],
  [39, 27, 51, 25],
  [39, 27, 46, 41],
  [51, 25, 63, 29],
  [51, 25, 57, 49],
  [63, 29, 74, 38],
  [63, 29, 57, 49],
  [74, 38, 79, 51],
  [74, 38, 69, 56],
  [79, 51, 69, 56],
  [69, 56, 57, 49],
  [69, 56, 71, 67],
  [57, 49, 46, 41],
  [57, 49, 46, 58],
  [35, 49, 46, 41],
  [35, 49, 46, 58],
  [46, 58, 58, 64],
  [58, 64, 71, 67],
  [58, 64, 55, 75],
];

export function BrainVisualization({
  name,
  lessonCount,
  className,
}: {
  name: string;
  lessonCount: number;
  className?: string;
}) {
  const firstName = name.split(" ")[0];

  return (
    <div
      className={cn(
        "brain-hero relative mx-auto flex aspect-square w-full max-w-md items-center justify-center",
        className,
      )}
    >
      <div className="brain-orb brain-orb-a" />
      <div className="brain-orb brain-orb-b" />
      <div className="brain-scan-line" aria-hidden />

      <img
        src={brainAnatomy}
        alt=""
        className="brain-anatomy absolute inset-0 z-10 h-full w-full object-contain"
        aria-hidden
      />

      <svg
        viewBox="0 0 100 100"
        className="brain-svg relative z-[12] h-full w-full drop-shadow-sm"
        aria-hidden
      >
        <defs>
          <filter id="brainGlow">
            <feGaussianBlur stdDeviation="1.4" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        {/* Neural pathways */}
        {SYNAPSES.map(([x1, y1, x2, y2], i) => (
          <line
            key={i}
            x1={x1}
            y1={y1}
            x2={x2}
            y2={y2}
            className="brain-synapse"
            stroke="oklch(0.6 0.16 245 / 0.55)"
            strokeWidth="0.28"
            style={{ animationDelay: `${i * 0.15}s` }}
          />
        ))}

        {/* Synaptic nodes */}
        {NODES.map((n, i) => (
          <g key={i} filter="url(#brainGlow)">
            <circle
              cx={n.cx}
              cy={n.cy}
              r={n.r}
              className="brain-node"
              fill="oklch(0.58 0.14 240)"
              style={{ animationDelay: `${n.delay}s` }}
            />
            <circle
              cx={n.cx}
              cy={n.cy}
              r={n.r * 2.5}
              className="brain-node-pulse"
              fill="oklch(0.58 0.14 240 / 0.25)"
              style={{ animationDelay: `${n.delay}s` }}
            />
          </g>
        ))}
      </svg>

      {/* Center label */}
      <div className="absolute inset-0 z-20 flex flex-col items-center justify-center text-center">
        <p className="text-[10px] font-semibold uppercase tracking-[0.35em] text-primary/80">
          Neural map
        </p>
        <p className="mt-1 font-display text-2xl font-bold tracking-tight text-gradient">
          {firstName}
        </p>
        <p className="mt-1 text-xs text-muted-foreground">{lessonCount} memories indexed</p>
      </div>

      {/* Orbiting memory chips */}
      <div className="brain-orbit brain-orbit-1">
        <span className="brain-chip">+ velocity</span>
      </div>
      <div className="brain-orbit brain-orbit-2">
        <span className="brain-chip">− vague advice</span>
      </div>
      <div className="brain-orbit brain-orbit-3">
        <span className="brain-chip">90-day lens</span>
      </div>
    </div>
  );
}
