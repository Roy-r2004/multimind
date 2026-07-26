import type { ReactNode } from "react";
import { Check } from "lucide-react";
import { VendorLogo } from "@/components/chat/VendorLogo";
import { cn } from "@/lib/utils";

export function CinematicBackdrop() {
  return (
    <div aria-hidden className="pointer-events-none fixed inset-0 -z-10 overflow-hidden bg-background">
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
    <div className={cn("flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between", className)}>
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
}: {
  children: ReactNode;
  className?: string;
  glow?: boolean;
  featured?: boolean;
}) {
  return (
    <div
      className={cn(
        "relative overflow-hidden rounded-2xl border border-border/90 bg-card/95 shadow-[0_1px_0_oklch(1_0_0/0.8)_inset,0_8px_28px_oklch(0.45_0.04_240/0.06)] backdrop-blur-[2px]",
        glow && "shadow-[0_1px_0_oklch(1_0_0/0.9)_inset,0_12px_36px_oklch(0.55_0.1_240/0.12)] ring-1 ring-primary/15",
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
}: {
  name: string;
  vendor: string;
  color: string;
  subtitle?: string;
}) {
  return (
    <div className="elevate-card group relative flex flex-col gap-3 rounded-2xl border border-border/90 bg-card/95 p-4 transition hover:border-primary/35 hover:shadow-md">
      <span className="absolute top-3 right-3 grid size-5 place-items-center rounded-full bg-primary/10 text-primary ring-1 ring-primary/20">
        <Check className="size-3" strokeWidth={3} />
      </span>
      <div className="flex items-center gap-3">
        <VendorLogo vendor={vendor} className="size-9 shrink-0 rounded-xl" title={name} />
        <div className="min-w-0">
          <div className="truncate font-medium text-foreground">{name}</div>
          <div className="text-xs text-muted-foreground">{vendor}</div>
          {subtitle && (
            <div className="truncate font-mono text-[10px] text-muted-foreground/80">{subtitle}</div>
          )}
        </div>
      </div>
      <span
        className="size-1.5 rounded-full shadow-[0_0_8px_currentColor]"
        style={{ color, background: color }}
      />
      <VendorLogo
        vendor={vendor}
        watermark
        className="pointer-events-none absolute -right-1 -bottom-1 size-14 opacity-30"
      />
    </div>
  );
}
