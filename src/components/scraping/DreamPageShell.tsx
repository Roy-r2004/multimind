import type { ButtonHTMLAttributes, ReactNode } from "react";
import { cn } from "@/lib/utils";

/** Content column for Scraping Council pages (sky comes from AppShell). */
export function DreamPageShell({
  children,
  className,
  maxWidth = "max-w-5xl",
}: {
  children: ReactNode;
  className?: string;
  maxWidth?: string;
  /** @deprecated intensity is controlled by AppShell live state; kept for call-site compat */
  intensity?: "calm" | "live";
}) {
  return (
    <div className={cn("relative mx-auto px-6 py-10", maxWidth, className)}>
      <div className="relative">{children}</div>
    </div>
  );
}

export function DreamHeader({
  eyebrow,
  title,
  description,
  action,
  className,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between",
        className,
      )}
    >
      <div>
        {eyebrow && (
          <p className="text-[11px] font-semibold uppercase tracking-[0.32em] text-sky-300/90">
            {eyebrow}
          </p>
        )}
        <h1 className="mt-1 font-display text-3xl tracking-tight text-white sm:text-4xl">
          {title}
        </h1>
        {description && (
          <p className="mt-2 max-w-2xl text-sm leading-relaxed text-slate-300/80">{description}</p>
        )}
      </div>
      {action}
    </div>
  );
}

/** Accent panel for CTAs that should feel airborne. */
export function DreamPanel({
  children,
  className,
  tone = "ink",
}: {
  children: ReactNode;
  className?: string;
  tone?: "ink" | "amber" | "teal";
}) {
  const tones = {
    ink: "council-glass-panel text-slate-100",
    amber:
      "border border-sky-300/30 bg-gradient-to-br from-sky-500/15 via-[#0b1224]/85 to-[#020617]/90 text-slate-100 shadow-[0_24px_60px_rgba(56,189,248,0.12)] backdrop-blur-md",
    teal: "border border-cyan-300/25 bg-gradient-to-br from-cyan-500/10 via-[#0b1224]/85 to-[#020617]/90 text-slate-100 backdrop-blur-md",
  };
  return (
    <div
      className={cn(
        "relative overflow-hidden rounded-[1.5rem] border p-6",
        tones[tone],
        className,
      )}
    >
      <div
        aria-hidden
        className="dream-drift pointer-events-none absolute -right-8 top-0 h-32 w-32 rounded-full blur-3xl"
        style={{ background: "rgba(56,189,248,0.12)" }}
      />
      <div className="relative">{children}</div>
    </div>
  );
}

export function DreamCta({
  children,
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { children: ReactNode }) {
  return (
    <button
      className={cn(
        "council-glass-cta inline-flex items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-semibold disabled:opacity-40",
        className,
      )}
      {...props}
    >
      {children}
    </button>
  );
}

/** Ghost control for secondary actions on dream surfaces. */
export const dreamGhostClass =
  "rounded-xl border border-white/15 bg-white/5 px-4 py-2.5 text-sm font-medium text-white backdrop-blur-sm transition hover:bg-white/10";

export const dreamDetailsClass =
  "rounded-2xl border border-white/10 bg-white/5 p-4 text-sm text-slate-100 backdrop-blur-md";

export const dreamInputClass =
  "w-full rounded-xl border border-white/15 bg-white/5 px-3 py-2 text-sm text-white outline-none placeholder:text-slate-500 focus:border-sky-300/50 focus:ring-2 focus:ring-sky-400/20";

export const dreamMutedClass = "text-sm text-slate-400";
