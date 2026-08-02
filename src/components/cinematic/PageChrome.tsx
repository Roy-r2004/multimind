import type { CSSProperties, ReactNode } from "react";
import { Check } from "lucide-react";
import { VendorLogo } from "@/components/chat/VendorLogo";
import { cn } from "@/lib/utils";

export function CinematicBackdrop() {
  return (
    <div
      aria-hidden
      className="pointer-events-none fixed inset-0 -z-10 overflow-hidden bg-background"
    >
      <div className="absolute -left-[15%] top-[-8%] h-[52vh] w-[52vh] rounded-full bg-sky-200/55 blur-[110px]" />
      <div className="absolute -right-[12%] top-[12%] h-[44vh] w-[44vh] rounded-full bg-blue-100/65 blur-[100px]" />
      <div className="absolute bottom-[-8%] left-[20%] h-[38vh] w-[48vh] rounded-full bg-sky-100/75 blur-[110px]" />
      <div className="absolute top-[40%] left-[45%] h-[28vh] w-[28vh] rounded-full bg-cyan-100/40 blur-[90px]" />
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_80%_60%_at_50%_-10%,oklch(0.96_0.03_235/0.7),transparent_55%)]" />
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,transparent_50%,oklch(0.98_0.01_240/0.55))]" />
    </div>
  );
}

export function PageHeader({
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
      className={cn("flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between", className)}
    >
      <div>
        {eyebrow && (
          <p className="text-[11px] font-semibold uppercase tracking-[0.32em] text-primary">
            {eyebrow}
          </p>
        )}
        <h1 className="mt-1.5 font-display text-3xl font-semibold tracking-tight text-foreground sm:text-[2.75rem] sm:leading-[1.1]">
          {title}
        </h1>
        {description && (
          <p className="mt-2.5 max-w-2xl text-sm leading-relaxed text-muted-foreground sm:text-[15px]">
            {description}
          </p>
        )}
      </div>
      {action}
    </div>
  );
}

export function GlassCard({
  children,
  className,
  glow,
  featured,
  style,
}: {
  children: ReactNode;
  className?: string;
  glow?: boolean;
  featured?: boolean;
  style?: CSSProperties;
}) {
  return (
    <div
      style={style}
      className={cn(
        "relative overflow-hidden rounded-2xl border border-border/90 bg-card/95 shadow-[0_1px_0_oklch(1_0_0/0.8)_inset,0_8px_28px_oklch(0.45_0.04_240/0.06)] backdrop-blur-[2px]",
        glow &&
          "shadow-[0_1px_0_oklch(1_0_0/0.9)_inset,0_12px_36px_oklch(0.55_0.1_240/0.12)] ring-1 ring-primary/15",
        featured && "ring-2 ring-primary/35 shadow-[0_12px_40px_oklch(0.55_0.12_240/0.14)]",
        className,
      )}
    >
      {children}
    </div>
  );
}

export function ModelPill({
  name,
  vendor,
  color,
  subtitle,
  variant = "default",
  index = 0,
}: {
  name: string;
  vendor: string;
  color: string;
  subtitle?: string;
  variant?: "default" | "cinematic";
  index?: number;
}) {
  if (variant === "cinematic") {
    const delay = Math.min(Math.max(index, 0), 4);
    return (
      <div
        className={cn("council-pill group", `council-pill-delay-${delay}`)}
        style={{ "--pill-accent": color } as CSSProperties}
      >
        <span aria-hidden className="council-pill-aurora" />
        <span aria-hidden className="council-pill-bloom" />
        <span aria-hidden className="council-pill-sheen" />
        <span className="council-pill-beacon">
          <Check className="size-3" strokeWidth={3} />
        </span>
        <div className="relative flex min-w-0 items-center gap-3.5 pr-6">
          <span className="council-pill-logo">
            <VendorLogo
              vendor={vendor}
              className="size-11 shrink-0 rounded-[0.95rem] shadow-[0_10px_28px_rgba(15,23,42,0.18)] ring-1 ring-white/85 transition-transform duration-500 group-hover:scale-110"
              title={name}
            />
          </span>
          <div className="min-w-0 flex-1">
            <div className="truncate font-display text-[15px] font-semibold leading-tight tracking-tight text-foreground">
              {name}
            </div>
            <div className="mt-1 truncate text-[10px] font-semibold uppercase tracking-[0.18em] text-muted-foreground/90">
              {vendor}
            </div>
            {subtitle && (
              <div className="mt-1 truncate font-mono text-[10px] text-muted-foreground/75">
                {subtitle}
              </div>
            )}
          </div>
        </div>
        <div className="relative mt-auto flex items-center gap-2 pt-1">
          <span aria-hidden className="council-pill-rail" />
          <span className="council-pill-dot" />
        </div>
      </div>
    );
  }

  return (
    <div className="elevate-card group relative flex min-h-20 flex-col justify-between gap-3 overflow-hidden rounded-2xl border border-border/80 bg-card/90 p-4 transition duration-300 hover:-translate-y-0.5 hover:border-primary/30 hover:bg-card hover:shadow-[0_14px_36px_oklch(0.55_0.08_240/0.12)]">
      <span className="absolute top-3 right-3 grid size-5 place-items-center rounded-full border border-primary/15 bg-primary/8 text-primary shadow-sm">
        <Check className="size-3" strokeWidth={3} />
      </span>
      <div className="relative flex min-w-0 items-center gap-3 pr-5">
        <VendorLogo
          vendor={vendor}
          className="size-10 shrink-0 rounded-[0.9rem] shadow-[0_6px_18px_rgba(15,23,42,0.14)] ring-1 ring-white/80 transition-transform duration-300 group-hover:scale-105"
          title={name}
        />
        <div className="min-w-0 flex-1">
          <div className="truncate font-medium leading-tight text-foreground">{name}</div>
          <div className="mt-1 truncate text-[11px] text-muted-foreground">{vendor}</div>
          {subtitle && (
            <div className="truncate font-mono text-[10px] text-muted-foreground/80">
              {subtitle}
            </div>
          )}
        </div>
      </div>
      <span
        className="size-1.5 rounded-full shadow-[0_0_8px_currentColor]"
        style={{ color, background: color }}
      />
    </div>
  );
}
