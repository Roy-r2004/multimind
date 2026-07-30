import type { ButtonHTMLAttributes, ReactNode } from "react";
import { ScrapingDreamSky } from "@/components/scraping/ScrapingDreamSky";
import { cn } from "@/lib/utils";

/** Content column for Scraping Council pages with a living light sky. */
export function DreamPageShell({
  children,
  className,
  maxWidth = "max-w-5xl",
  intensity = "calm",
}: {
  children: ReactNode;
  className?: string;
  maxWidth?: string;
  intensity?: "calm" | "live";
}) {
  return (
    <div className={cn("relative mx-auto px-6 py-10", maxWidth, className)}>
      {/* Live intensity overlays denser sky motion over the shell backdrop. */}
      {intensity === "live" && (
        <ScrapingDreamSky intensity="live" className="pointer-events-none fixed inset-0 -z-10 opacity-90" />
      )}
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
          <p className="text-[11px] font-semibold uppercase tracking-[0.32em] text-primary">
            {eyebrow}
          </p>
        )}
        <h1 className="mt-1 font-display text-3xl font-semibold tracking-tight text-foreground sm:text-4xl">
          {title}
        </h1>
        {description && (
          <p className="mt-2 max-w-2xl text-sm leading-relaxed text-muted-foreground">
            {description}
          </p>
        )}
      </div>
      {action}
    </div>
  );
}

/** Accent panel aligned with light GlassCard language. */
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
    ink: "border-border/90 bg-card/95 text-foreground shadow-[0_8px_28px_oklch(0.45_0.04_240/0.06)]",
    amber:
      "border-primary/25 bg-gradient-to-br from-sky-50 via-card to-blue-50 text-foreground shadow-[0_12px_36px_oklch(0.55_0.1_240/0.1)]",
    teal: "border-teal-300/40 bg-gradient-to-br from-teal-50/90 via-card to-sky-50 text-foreground",
  };
  return (
    <div
      className={cn(
        "relative overflow-hidden rounded-[1.5rem] border p-6 backdrop-blur-[2px]",
        tones[tone],
        className,
      )}
    >
      <div
        aria-hidden
        className="dream-drift pointer-events-none absolute -right-8 top-0 h-32 w-32 rounded-full blur-3xl"
        style={{ background: "oklch(0.72 0.1 240 / 0.12)" }}
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
        "inline-flex items-center justify-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-semibold text-primary-foreground shadow-sm transition hover:bg-primary/90 disabled:opacity-40",
        className,
      )}
      {...props}
    >
      {children}
    </button>
  );
}

export const dreamGhostClass =
  "rounded-xl border border-border bg-card px-4 py-2.5 text-sm font-medium text-foreground shadow-sm transition hover:bg-accent";

export const dreamDetailsClass =
  "rounded-2xl border border-border bg-muted/30 p-4 text-sm text-foreground";

export const dreamInputClass =
  "w-full rounded-xl border border-border bg-card px-3 py-2 text-sm text-foreground outline-none placeholder:text-muted-foreground focus:border-primary focus:ring-2 focus:ring-primary/20";

export const dreamMutedClass = "text-sm text-muted-foreground";
