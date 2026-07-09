import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export function CinematicBackdrop() {
  return (
    <div aria-hidden className="pointer-events-none fixed inset-0 -z-10 overflow-hidden bg-background">
      <div className="absolute -left-[15%] top-[-8%] h-[50vh] w-[50vh] rounded-full bg-sky-200/50 blur-[100px]" />
      <div className="absolute -right-[10%] top-[15%] h-[40vh] w-[40vh] rounded-full bg-blue-100/60 blur-[90px]" />
      <div className="absolute bottom-[-5%] left-[25%] h-[35vh] w-[45vh] rounded-full bg-sky-100/70 blur-[100px]" />
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
          <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-primary">
            {eyebrow}
          </p>
        )}
        <h1 className="mt-1 text-3xl font-semibold tracking-tight text-foreground sm:text-4xl">
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

export function GlassCard({
  children,
  className,
  glow,
}: {
  children: ReactNode;
  className?: string;
  glow?: boolean;
}) {
  return (
    <div
      className={cn(
        "relative overflow-hidden rounded-2xl border border-border bg-card shadow-sm",
        glow && "shadow-md shadow-primary/10 ring-1 ring-primary/10",
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
    <div className="group relative flex flex-col gap-3 rounded-2xl border border-border bg-card p-4 transition hover:border-primary/30 hover:shadow-sm">
      <div className="flex items-center gap-3">
        <span
          className="size-3 shrink-0 rounded-full"
          style={{ color, background: color }}
        />
        <div className="min-w-0">
          <div className="truncate font-medium text-foreground">{name}</div>
          <div className="text-xs text-muted-foreground">{vendor}</div>
          {subtitle && (
            <div className="truncate font-mono text-[10px] text-muted-foreground/80">{subtitle}</div>
          )}
        </div>
      </div>
    </div>
  );
}
