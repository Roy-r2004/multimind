import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export function CinematicBackdrop() {
  return (
    <div aria-hidden className="pointer-events-none fixed inset-0 -z-10 overflow-hidden">
      <div className="absolute -left-[20%] top-[-10%] h-[55vh] w-[55vh] rounded-full bg-primary/20 blur-[120px]" />
      <div className="absolute -right-[15%] top-[20%] h-[45vh] w-[45vh] rounded-full bg-violet-500/15 blur-[100px]" />
      <div className="absolute bottom-[-10%] left-[30%] h-[40vh] w-[50vh] rounded-full bg-cyan-500/10 blur-[110px]" />
      <div className="absolute inset-0 bg-[url('data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIzMDAiIGhlaWdodD0iMzAwIj48ZmlsdGVyIGlkPSJhIj48ZmVUdXJidWxlbmNlIHR5cGU9ImZyYWN0YWxOb2lzZSIgYmFzZUZyZXF1ZW5jeT0iLjc1IiBudW1PY3RhdmVzPSIzIiBzdGl0Y2hUaWxlcz0ic3RpdGNoIi8+PGZlQ29sb3JNYXRyaXggdHlwZT0ic2F0dXJhdGUiIHZhbHVlcz0iMCIvPjwvZmlsdGVyPjxyZWN0IHdpZHRoPSIzMDAiIGhlaWdodD0iMzAwIiBmaWx0ZXI9InVybCgjYSkiIG9wYWNpdHk9Ii4wNCIvPjwvc3ZnPg==')] opacity-40" />
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
          <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-primary/80">
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
        "relative overflow-hidden rounded-2xl border border-white/10 bg-card/60 backdrop-blur-xl",
        glow && "shadow-[0_0_60px_-12px] shadow-primary/25",
        className,
      )}
    >
      {glow && (
        <div className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-primary/60 to-transparent" />
      )}
      {children}
    </div>
  );
}

export function ModelPill({
  name,
  vendor,
  color,
  pricing,
  subtitle,
}: {
  name: string;
  vendor: string;
  color: string;
  pricing?: { input_per_1k: number; output_per_1k: number } | null;
  subtitle?: string;
}) {
  return (
    <div className="group relative flex flex-col gap-3 rounded-2xl border border-white/10 bg-background/40 p-4 transition hover:border-primary/40 hover:bg-background/60">
      <div className="flex items-center gap-3">
        <span
          className="size-3 shrink-0 rounded-full shadow-[0_0_12px_currentColor]"
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
      {pricing && (
        <div className="flex justify-between text-[11px] text-muted-foreground">
          <span>In ${pricing.input_per_1k.toFixed(4)}/1K</span>
          <span>Out ${pricing.output_per_1k.toFixed(4)}/1K</span>
        </div>
      )}
    </div>
  );
}
