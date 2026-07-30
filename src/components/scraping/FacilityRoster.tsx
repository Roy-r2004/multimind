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
        facility.primary_website,
        facility.verification_status,
        facility.publication_class,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return haystack.includes(q);
    });
  }, [facilities, query, typeFilter]);

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden rounded-[1.5rem] border border-border bg-card shadow-sm">
      <div className="shrink-0 border-b border-border p-4">
        <div className="flex items-center justify-between gap-2">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-primary">
              Crystallized
            </p>
            <h2 className="font-display text-lg text-foreground">Facilities</h2>
          </div>
          <span className="rounded-full border border-border bg-muted px-2.5 py-0.5 text-xs font-medium text-foreground">
            {filtered.length}
          </span>
        </div>
        <div className="mt-3 grid gap-2">
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search name, city, contact…"
            className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground outline-none placeholder:text-muted-foreground focus:border-primary/50"
          />
          <select
            value={typeFilter}
            onChange={(event) => setTypeFilter(event.target.value)}
            className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground outline-none focus:border-primary/50"
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
      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
        {filtered.length === 0 ? (
          <p className="p-4 text-sm text-muted-foreground">No facilities match this filter.</p>
        ) : (
          <ul className="divide-y divide-border">
            {filtered.map((facility) => {
              const selected = facility.id === selectedId;
              const place = [facility.primary_city, facility.primary_region]
                .filter(Boolean)
                .join(", ");
              const chips = buildFacilityChips(facility);
              return (
                <li key={facility.id}>
                  <button
                    type="button"
                    onClick={() => onSelect(facility.id)}
                    className={cn(
                      "w-full px-4 py-3 text-left transition",
                      selected ? "bg-primary/10" : "hover:bg-muted/60",
                    )}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0 flex-1">
                        <p className="truncate font-medium text-foreground">
                          {facility.canonical_name}
                        </p>
                        <p className="mt-0.5 truncate text-xs text-muted-foreground">
                          {facility.facility_type}
                          {place ? ` · ${place}` : ""}
                        </p>
                        {facility.primary_contact ? (
                          <p className="mt-1 truncate text-xs text-foreground/80">
                            {facility.primary_contact}
                          </p>
                        ) : facility.primary_website ? (
                          <p className="mt-1 truncate text-xs text-foreground/80">
                            {facility.primary_website}
                          </p>
                        ) : null}
                        {chips.length > 0 ? (
                          <div className="mt-2 flex flex-wrap gap-1.5">
                            {chips.map((chip) => (
                              <Chip key={chip.label} label={chip.label} tone={chip.tone} />
                            ))}
                          </div>
                        ) : null}
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

function buildFacilityChips(facility: ScrapingFacilitySummary): Array<{
  label: string;
  tone: "neutral" | "good" | "warn";
}> {
  const chips: Array<{ label: string; tone: "neutral" | "good" | "warn" }> = [];
  const status = (facility.verification_status || "").toLowerCase();
  if (status.includes("review") || facility.publication_class === "review_required") {
    chips.push({ label: "Review", tone: "warn" });
  } else if (status && !status.includes("verif")) {
    chips.push({ label: status.replaceAll("_", " "), tone: "neutral" });
  }

  const locations = facility.location_count ?? 0;
  const contacts = facility.contact_count ?? 0;
  const services = facility.treatment_service_count ?? 0;
  if (locations > 0) chips.push({ label: `${locations} loc`, tone: "neutral" });
  if (contacts > 0) chips.push({ label: `${contacts} contact`, tone: "neutral" });
  if (services > 0) chips.push({ label: `${services} services`, tone: "neutral" });

  if (!facility.primary_contact) {
    chips.push({ label: "No phone", tone: "warn" });
  }
  return chips.slice(0, 4);
}

function Chip({
  label,
  tone,
}: {
  label: string;
  tone: "neutral" | "good" | "warn";
}) {
  return (
    <span
      className={cn(
        "rounded-md border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide",
        tone === "good" && "border-teal-600/30 bg-teal-50 text-teal-800",
        tone === "warn" && "border-amber-600/30 bg-amber-50 text-amber-900",
        tone === "neutral" && "border-border bg-muted text-foreground/85",
      )}
    >
      {label}
    </span>
  );
}
