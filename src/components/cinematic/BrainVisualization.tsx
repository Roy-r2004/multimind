import { cn } from "@/lib/utils";

const NODES = [
  { cx: 50, cy: 28, r: 3, delay: 0 },
  { cx: 38, cy: 38, r: 2.5, delay: 0.4 },
  { cx: 62, cy: 38, r: 2.5, delay: 0.8 },
  { cx: 32, cy: 52, r: 2, delay: 1.2 },
  { cx: 68, cy: 52, r: 2, delay: 1.6 },
  { cx: 42, cy: 62, r: 2.5, delay: 0.6 },
  { cx: 58, cy: 62, r: 2.5, delay: 1.0 },
  { cx: 50, cy: 72, r: 3, delay: 1.4 },
];

const SYNAPSES: Array<[number, number, number, number]> = [
  [50, 28, 38, 38],
  [50, 28, 62, 38],
  [38, 38, 32, 52],
  [62, 38, 68, 52],
  [32, 52, 42, 62],
  [68, 52, 58, 62],
  [42, 62, 50, 72],
  [58, 62, 50, 72],
  [50, 28, 50, 72],
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

      <svg
        viewBox="0 0 100 100"
        className="brain-svg relative z-10 h-full w-full drop-shadow-sm"
        aria-hidden
      >
        <defs>
          <linearGradient id="brainWire" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="oklch(0.58 0.14 240)" stopOpacity="0.9" />
            <stop offset="100%" stopColor="oklch(0.72 0.12 200)" stopOpacity="0.5" />
          </linearGradient>
          <filter id="brainGlow">
            <feGaussianBlur stdDeviation="1.2" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        {/* Wireframe brain silhouette */}
        <path
          className="brain-outline"
          d="M50 12 C34 12 22 24 20 38 C16 42 14 48 16 56 C14 64 18 72 26 76 C28 84 38 90 50 90 C62 90 72 84 74 76 C82 72 86 64 84 56 C86 48 84 42 80 38 C78 24 66 12 50 12 Z"
          fill="none"
          stroke="url(#brainWire)"
          strokeWidth="0.6"
          strokeDasharray="3 2"
        />
        <path
          className="brain-fill"
          d="M50 18 C36 18 26 28 24 40 C20 44 18 50 20 56 C18 62 22 68 30 72 C32 80 40 84 50 84 C60 84 68 80 70 72 C78 68 82 62 80 56 C82 50 80 44 76 40 C74 28 64 18 50 18 Z"
          fill="oklch(0.58 0.14 240 / 0.06)"
        />

        {/* Neural pathways */}
        {SYNAPSES.map(([x1, y1, x2, y2], i) => (
          <line
            key={i}
            x1={x1}
            y1={y1}
            x2={x2}
            y2={y2}
            className="brain-synapse"
            stroke="oklch(0.58 0.14 240 / 0.35)"
            strokeWidth="0.35"
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
        <p className="text-[10px] font-semibold uppercase tracking-[0.35em] text-primary/80">Neural map</p>
        <p className="mt-1 font-display text-2xl font-bold tracking-tight text-gradient">{firstName}</p>
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
