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
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 -top-10 h-48 bg-gradient-to-b from-[#0b161c]/55 to-transparent"
      />
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
          <p className="text-[11px] font-semibold uppercase tracking-[0.32em] text-[#d4a84b]">
            {eyebrow}
          </p>
        )}
        <h1 className="mt-1 font-display text-3xl font-semibold tracking-tight text-[#f7f1e4] sm:text-4xl">
          {title}
        </h1>
        {description && (
          <p className="mt-2 max-w-2xl text-sm leading-relaxed text-white/55">{description}</p>
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
    ink: "border-white/10 bg-[#0b161c]/75 text-[#f7f1e4] shadow-[0_24px_60px_rgba(0,0,0,0.28)] backdrop-blur-md",
    amber:
      "border-[#d4a84b]/35 bg-gradient-to-br from-[#2a2416]/95 via-[#1a2218]/90 to-[#0b161c]/90 text-[#f7f1e4] shadow-[0_24px_60px_rgba(212,168,75,0.12)] backdrop-blur-md",
    teal: "border-teal-300/25 bg-gradient-to-br from-[#16343a]/95 via-[#102229]/90 to-[#0b161c]/90 text-[#f7f1e4] backdrop-blur-md",
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
        style={{ background: "rgba(212,168,75,0.12)" }}
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
        "inline-flex items-center justify-center gap-2 rounded-xl bg-[#d4a84b] px-4 py-2.5 text-sm font-semibold text-[#0b161c]",
        "shadow-[0_12px_28px_rgba(212,168,75,0.28)] transition hover:bg-[#e0b85c] disabled:opacity-40",
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
  "rounded-xl border border-white/15 bg-white/5 px-4 py-2.5 text-sm font-medium text-[#f7f1e4] backdrop-blur-sm transition hover:bg-white/10";

export const dreamDetailsClass =
  "rounded-2xl border border-white/10 bg-[#0b161c]/60 p-4 text-sm text-[#f7f1e4] backdrop-blur-md";

export const dreamInputClass =
  "w-full rounded-xl border border-white/15 bg-white/5 px-3 py-2 text-sm text-[#f7f1e4] outline-none placeholder:text-white/30 focus:border-[#d4a84b]/50 focus:ring-2 focus:ring-[#d4a84b]/20";

export const dreamMutedClass = "text-sm text-white/55";
