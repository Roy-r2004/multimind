import { cn } from "@/lib/utils";

export function MockBanner({
  children,
  className,
}: {
  children?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "mock-banner relative overflow-hidden rounded-2xl border border-amber-200/80 bg-gradient-to-r from-amber-50 via-white to-amber-50 px-4 py-3 text-sm text-amber-950",
        className,
      )}
    >
      <div className="mock-banner-shine pointer-events-none absolute inset-0" aria-hidden />
      <p>
        <span className="font-semibold">Mock preview</span>
        {children ?? " — sample UI only. Not connected to live data yet."}
      </p>
    </div>
  );
}
