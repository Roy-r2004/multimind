import { useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import type { ScrapingFacilitySummary } from "@/lib/scraping/types";

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
    <div className="flex h-full min-h-[28rem] flex-col rounded-xl border border-border bg-card/40">
      <div className="border-b border-border p-4">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-lg font-semibold">Facilities</h2>
          <Badge variant="secondary">{filtered.length}</Badge>
        </div>
        <div className="mt-3 grid gap-2">
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search name, city, contact…"
            className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none ring-offset-background focus-visible:ring-2 focus-visible:ring-ring"
          />
          <select
            value={typeFilter}
            onChange={(event) => setTypeFilter(event.target.value)}
            className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none ring-offset-background focus-visible:ring-2 focus-visible:ring-ring"
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
          <p className="p-4 text-sm text-muted-foreground">No facilities match this filter.</p>
        ) : (
          <ul className="divide-y divide-border">
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
                    className={`w-full px-4 py-3 text-left transition-colors ${
                      selected ? "bg-primary/10" : "hover:bg-muted/40"
                    }`}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="truncate font-medium">{facility.canonical_name}</p>
                        <p className="mt-0.5 text-xs text-muted-foreground">
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
                      <span className="shrink-0 text-sm font-semibold tabular-nums">
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
    <span className="rounded-md border border-border/80 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
      {label}
    </span>
  );
}
