import { useEffect, useState } from "react";
import { getCountryOutlinePath, getFlagColors } from "@/lib/maps/countryVisuals";
import { cn } from "@/lib/utils";

export function CountryOutline({
  countryCode,
  className,
}: {
  countryCode: string;
  className?: string;
}) {
  const [path, setPath] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setPath(null);
    void getCountryOutlinePath(countryCode).then((d) => {
      if (!cancelled) setPath(d);
    });
    return () => {
      cancelled = true;
    };
  }, [countryCode]);

  if (!path) return null;

  const [primary, secondary] = getFlagColors(countryCode);
  const gradientId = `country-outline-gradient-${countryCode.toLowerCase()}`;

  return (
    <svg
      viewBox="0 0 200 200"
      aria-hidden="true"
      className={cn("pointer-events-none select-none", className)}
    >
      <defs>
        <linearGradient id={gradientId} x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor={primary} stopOpacity="0.55" />
          <stop offset="100%" stopColor={secondary} stopOpacity="0.35" />
        </linearGradient>
      </defs>
      <path
        d={path}
        fill={`url(#${gradientId})`}
        stroke={primary}
        strokeOpacity="0.6"
        strokeWidth="1.5"
      />
    </svg>
  );
}
