import type { ReactNode } from "react";
import { Check } from "lucide-react";
import { VendorLogo } from "@/components/chat/VendorLogo";
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

export function GlassCard({
  children,
  className,
  glow,
  variant = "default",
}: {
  children: ReactNode;
  className?: string;
  glow?: boolean;
  variant?: "default" | "council";
}) {
  return (
    <div
      className={cn(
        "relative overflow-hidden rounded-2xl border shadow-sm",
        variant === "council"
          ? "council-glass-panel text-slate-100"
          : "border-border bg-card",
        glow && variant === "default" && "shadow-md shadow-primary/10 ring-1 ring-primary/10",
        glow && variant === "council" && "ring-1 ring-sky-400/20",
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
}: {
  name: string;
  vendor: string;
  color: string;
  subtitle?: string;
  variant?: "default" | "council";
}) {
  const glass = variant === "council";
  return (
    <div
      className={cn(
        "group relative flex flex-col gap-3 rounded-2xl p-4 transition",
        glass
          ? "council-glass-chip hover:border-sky-300/30 hover:shadow-[0_0_30px_rgb(56_189_248_/_0.15)]"
          : "border border-border bg-card hover:border-primary/30 hover:shadow-sm",
      )}
    >
      {glass && (
        <span className="absolute top-3 right-3 grid size-5 place-items-center rounded-full bg-sky-400/20 text-sky-200 ring-1 ring-sky-300/40">
          <Check className="size-3" strokeWidth={3} />
        </span>
      )}
      <div className="flex items-center gap-3">
        {glass ? (
          <VendorLogo vendor={vendor} className="size-9 shrink-0 rounded-xl" />
        ) : (
          <span
            className="size-3 shrink-0 rounded-full shadow-[0_0_16px_currentColor]"
            style={{ color, background: color }}
          />
        )}
        <div className="min-w-0">
          <div
            className={cn(
              "truncate font-medium",
              glass ? "text-white" : "text-foreground",
            )}
          >
            {name}
          </div>
          <div className={cn("text-xs", glass ? "text-slate-300/80" : "text-muted-foreground")}>
            {vendor}
          </div>
          {subtitle && (
            <div
              className={cn(
                "truncate font-mono text-[10px]",
                glass ? "text-slate-400/80" : "text-muted-foreground/80",
              )}
            >
              {subtitle}
            </div>
          )}
        </div>
      </div>
      {glass && (
        <VendorLogo
          vendor={vendor}
          watermark
          className="pointer-events-none absolute -right-1 -bottom-1 size-16"
        />
      )}
    </div>
  );
}
