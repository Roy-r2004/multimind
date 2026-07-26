import { useMemo, useState } from "react";
import type { ScrapingFacilitySummary } from "@/lib/scraping/types";
import { cn } from "@/lib/utils";

type Props = {
  facilities: ScrapingFacilitySummary[];
  selectedId: string | null;
  onSelect: (facilityId: string) => void;
};

export function FacilityRoster({ facilities, selectedId, onSelect }: Props) {
  const [query, setQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState("all");

  const types = useMemo(() => {
    return Array.from(new Set(facilities.map((f) => f.facility_type))).sort();
  }, [facilities]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return facilities.filter((facility) => {
      if (typeFilter !== "all" && facility.facility_type !== typeFilter) return false;
      if (!q) return true;
      const haystack = [
        facility.canonical_name,
        facility.primary_city,
        facility.primary_region,
        facility.facility_type,
        facility.primary_contact,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return haystack.includes(q);
    });
  }, [facilities, query, typeFilter]);

  return (
    <div className="flex h-full min-h-[28rem] flex-col overflow-hidden rounded-[1.5rem] border border-border bg-card/75 shadow-[0_20px_50px_rgba(0,0,0,0.25)] backdrop-blur-md">
      <div className="border-b border-border p-4">
        <div className="flex items-center justify-between gap-2">
          <div>
            <p className="text-[11px] uppercase tracking-[0.28em] text-primary/90">Crystallized</p>
            <h2 className="font-display text-lg text-foreground">Facilities</h2>
          </div>
          <span className="rounded-full border border-border bg-muted/40 px-2.5 py-0.5 text-xs text-foreground/80">
            {filtered.length}
          </span>
        </div>
        <div className="mt-3 grid gap-2">
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search name, city, contact…"
            className="w-full rounded-xl border border-border bg-muted/40 px-3 py-2 text-sm text-foreground outline-none placeholder:text-muted-foreground focus:border-primary/50"
          />
          <select
            value={typeFilter}
            onChange={(event) => setTypeFilter(event.target.value)}
            className="w-full rounded-xl border border-border bg-muted/40 px-3 py-2 text-sm text-foreground outline-none focus:border-primary/50"
          >
            <option value="all">All types</option>
            {types.map((type) => (
              <option key={type} value={type}>
                {type}
              </option>
            ))}
          </select>
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        {filtered.length === 0 ? (
          <p className="p-4 text-sm text-white/45">No facilities match this filter.</p>
        ) : (
          <ul className="divide-y divide-white/10">
            {filtered.map((facility) => {
              const selected = facility.id === selectedId;
              const place = [facility.primary_city, facility.primary_region]
                .filter(Boolean)
                .join(", ");
              return (
                <li key={facility.id}>
                  <button
                    type="button"
                    onClick={() => onSelect(facility.id)}
                    className={cn(
                      "w-full px-4 py-3 text-left transition",
                      selected
                        ? "bg-primary/12"
                        : "hover:bg-white/[0.04]",
                    )}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="truncate font-medium text-foreground">
                          {facility.canonical_name}
                        </p>
                        <p className="mt-0.5 text-xs text-white/45">
                          {facility.facility_type}
                          {place ? ` · ${place}` : ""}
                        </p>
                        {facility.primary_contact ? (
                          <p className="mt-1 truncate text-xs text-muted-foreground">
                            {facility.primary_contact}
                          </p>
                        ) : null}
                        <div className="mt-2 flex flex-wrap gap-1.5">
                          <Chip label={`${facility.location_count ?? 0} loc`} />
                          <Chip label={`${facility.contact_count ?? 0} contact`} />
                          <Chip label={`${facility.treatment_service_count ?? 0} services`} />
                        </div>
                      </div>
                      <span className="shrink-0 text-sm font-semibold tabular-nums text-primary">
                        {(facility.confidence_score * 100).toFixed(0)}%
                      </span>
                    </div>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}

function Chip({ label }: { label: string }) {
  return (
    <span className="rounded-md border border-border px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-white/45">
      {label}
    </span>
  );
}
