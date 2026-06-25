import { useEffect, useState, type ReactNode } from "react";
import { cn } from "@/lib/utils";

export function SkeletonReveal({
  children,
  delayMs = 0,
  className,
}: {
  children: ReactNode;
  delayMs?: number;
  className?: string;
}) {
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const t = window.setTimeout(() => setReady(true), delayMs);
    return () => window.clearTimeout(t);
  }, [delayMs]);

  return (
    <div className={cn("relative", className)}>
      {!ready && (
        <div className="space-y-3" aria-hidden>
          <div className="skeleton-shimmer h-4 w-3/4 rounded-lg" />
          <div className="skeleton-shimmer h-4 w-full rounded-lg" />
          <div className="skeleton-shimmer h-4 w-5/6 rounded-lg" />
        </div>
      )}
      <div
        className={cn(
          "transition-all duration-700 ease-out",
          ready ? "translate-y-0 opacity-100" : "pointer-events-none absolute inset-0 translate-y-2 opacity-0",
        )}
      >
        {children}
      </div>
    </div>
  );
}
