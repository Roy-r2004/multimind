import { useEffect, useMemo, useState } from "react";
import { cn } from "@/lib/utils";

export type ExplosionMode = "full" | "replay" | null;

type Props = {
  mode: ExplosionMode;
  onDone?: () => void;
  label?: string;
};

const PARTICLE_COUNT = { full: 72, replay: 36 } as const;

/**
 * Full-viewport light burst when a census lands (or a shorter replay when
 * reopening completed results). Intentionally loud — this is the payoff beat.
 */
export function ResultsExplosion({ mode, onDone, label }: Props) {
  const [visible, setVisible] = useState(Boolean(mode));

  const particles = useMemo(() => {
    if (!mode) return [];
    const count = PARTICLE_COUNT[mode];
    return Array.from({ length: count }, (_, i) => {
      const angle = (Math.PI * 2 * i) / count + (i % 3) * 0.17;
      const distance = mode === "full" ? 38 + (i % 7) * 9 : 26 + (i % 5) * 7;
      const size = mode === "full" ? 6 + (i % 5) * 3 : 4 + (i % 4) * 2;
      const hue = i % 3 === 0 ? 190 : i % 3 === 1 ? 155 : 42;
      return {
        id: i,
        x: Math.cos(angle) * distance,
        y: Math.sin(angle) * distance,
        size,
        hue,
        delay: (i % 12) * 0.018,
        duration: mode === "full" ? 1.35 + (i % 5) * 0.12 : 0.85 + (i % 4) * 0.08,
      };
    });
  }, [mode]);

  useEffect(() => {
    if (!mode) {
      setVisible(false);
      return;
    }
    setVisible(true);
    const ms = mode === "full" ? 2400 : 1400;
    const timer = window.setTimeout(() => {
      setVisible(false);
      onDone?.();
    }, ms);
    return () => window.clearTimeout(timer);
  }, [mode, onDone]);

  if (!mode || !visible) return null;

  return (
    <div
      aria-hidden
      className={cn(
        "pointer-events-none fixed inset-0 z-[80] overflow-hidden",
        mode === "full" ? "scrape-explosion-full" : "scrape-explosion-replay",
      )}
    >
      <div className="scrape-explosion-flash absolute inset-0" />
      <div className="scrape-explosion-shockwave absolute left-1/2 top-[42%] -translate-x-1/2 -translate-y-1/2" />
      <div className="scrape-explosion-shockwave scrape-explosion-shockwave-delay absolute left-1/2 top-[42%] -translate-x-1/2 -translate-y-1/2" />
      <div className="scrape-explosion-core absolute left-1/2 top-[42%] -translate-x-1/2 -translate-y-1/2" />

      {Array.from({ length: mode === "full" ? 8 : 4 }).map((_, i) => (
        <span
          key={`ray-${i}`}
          className="scrape-explosion-ray absolute left-1/2 top-[42%] origin-bottom"
          style={{
            transform: `translate(-50%, -100%) rotate(${(360 / (mode === "full" ? 8 : 4)) * i}deg)`,
            animationDelay: `${i * 0.04}s`,
          }}
        />
      ))}

      {particles.map((p) => (
        <span
          key={p.id}
          className="scrape-explosion-particle absolute left-1/2 top-[42%] rounded-full"
          style={{
            width: p.size,
            height: p.size,
            background: `oklch(0.78 0.14 ${p.hue})`,
            boxShadow: `0 0 ${p.size * 2}px oklch(0.82 0.12 ${p.hue} / 0.85)`,
            ["--tx" as string]: `${p.x}vmin`,
            ["--ty" as string]: `${p.y}vmin`,
            animationDuration: `${p.duration}s`,
            animationDelay: `${p.delay}s`,
          }}
        />
      ))}

      <div className="scrape-explosion-caption absolute inset-x-0 top-[58%] text-center">
        <p className="font-display text-3xl font-semibold tracking-tight text-slate-800 drop-shadow-sm sm:text-5xl">
          {label ?? (mode === "full" ? "Results crystallized" : "Welcome back")}
        </p>
        <p className="mt-2 text-sm tracking-[0.22em] text-slate-600 uppercase">
          {mode === "full" ? "Census complete" : "Reopening flight"}
        </p>
      </div>
    </div>
  );
}
