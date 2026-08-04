import { ChevronDown, ChevronUp, MapPin, Phone, Globe } from "lucide-react";
import { useState } from "react";
import type { MapsPlaceItem } from "@/lib/maps/types";
import { cn } from "@/lib/utils";

type GroupedFacility = {
  organizationName: string;
  mainFacility: MapsPlaceItem;
  branches: MapsPlaceItem[];
};

function groupFacilitiesByOrganization(places: MapsPlaceItem[]): GroupedFacility[] {
  const groups = new Map<string, { main: MapsPlaceItem; branches: MapsPlaceItem[] }>();

  places.forEach((place) => {
    // Extract organization name (everything before the dash or first location name)
    const orgName = place.canonical_name.split(" - ")[0] || place.canonical_name;

    if (!groups.has(orgName)) {
      groups.set(orgName, { main: place, branches: [] });
    } else {
      const group = groups.get(orgName)!;
      // If this is inpatient/residential and current main isn't, swap
      if (place.care_setting === "residential" && group.main.care_setting !== "residential") {
        group.branches.push(group.main);
        group.main = place;
      } else {
        group.branches.push(place);
      }
    }
  });

  return Array.from(groups.entries()).map(([orgName, { main, branches }]) => ({
    organizationName: orgName,
    mainFacility: main,
    branches,
  }));
}

export function FacilityNetworkView({ places }: { places: MapsPlaceItem[] }) {
  const [expandedOrgs, setExpandedOrgs] = useState<Set<string>>(new Set());

  const grouped = groupFacilitiesByOrganization(places);

  const toggleOrg = (orgName: string) => {
    const newSet = new Set(expandedOrgs);
    if (newSet.has(orgName)) {
      newSet.delete(orgName);
    } else {
      newSet.add(orgName);
    }
    setExpandedOrgs(newSet);
  };

  return (
    <div className="space-y-6">
      {grouped.map((group) => (
        <div key={group.organizationName} className="space-y-4">
          {/* Main Facility Card */}
          <div className="rounded-2xl border border-emerald-200/50 bg-gradient-to-br from-emerald-50/80 to-emerald-50/40 p-5 shadow-sm dark:border-emerald-900/40 dark:from-emerald-950/30 dark:to-emerald-950/10">
            <div className="flex items-start justify-between gap-4">
              <div className="flex-1">
                <div className="flex items-center gap-2">
                  <h3 className="font-display text-lg font-semibold text-foreground">
                    {group.mainFacility.canonical_name}
                  </h3>
                  {group.mainFacility.care_setting === "residential" && (
                    <span className="inline-flex items-center rounded-full bg-emerald-600 px-2.5 py-0.5 text-xs font-semibold text-white">
                      ⭐ INPATIENT
                    </span>
                  )}
                </div>

                <div className="mt-3 space-y-1.5 text-sm">
                  {group.mainFacility.formatted_address && (
                    <div className="flex items-start gap-2 text-muted-foreground">
                      <MapPin className="size-4 mt-0.5 flex-shrink-0" />
                      <span>{group.mainFacility.formatted_address}</span>
                    </div>
                  )}
                  {group.mainFacility.international_phone_number && (
                    <div className="flex items-center gap-2 text-muted-foreground">
                      <Phone className="size-4" />
                      <a
                        href={`tel:${group.mainFacility.international_phone_number}`}
                        className="hover:text-foreground"
                      >
                        {group.mainFacility.international_phone_number}
                      </a>
                    </div>
                  )}
                  {group.mainFacility.official_website && (
                    <div className="flex items-center gap-2">
                      <Globe className="size-4 text-muted-foreground" />
                      <a
                        href={group.mainFacility.official_website}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-blue-600 hover:underline dark:text-blue-400"
                      >
                        {group.mainFacility.official_website.replace(/^https?:\/\//, "")}
                      </a>
                    </div>
                  )}
                </div>

                {group.mainFacility.addictions_treated && group.mainFacility.addictions_treated.length > 0 && (
                  <div className="mt-3 pt-3 border-t border-emerald-200/50 dark:border-emerald-900/40">
                    <p className="text-xs font-semibold text-muted-foreground uppercase">Specialties</p>
                    <p className="mt-1 text-sm text-foreground">
                      {group.mainFacility.addictions_treated.slice(0, 5).join(", ")}
                      {group.mainFacility.addictions_treated.length > 5 && "..."}
                    </p>
                  </div>
                )}

                {group.mainFacility.languages_spoken && group.mainFacility.languages_spoken.length > 0 && (
                  <div className="mt-2">
                    <p className="text-xs font-semibold text-muted-foreground uppercase">Languages</p>
                    <p className="mt-1 text-sm text-foreground">
                      {group.mainFacility.languages_spoken.slice(0, 4).join(", ")}
                      {group.mainFacility.languages_spoken.length > 4 && "..."}
                    </p>
                  </div>
                )}
              </div>
            </div>

            {/* Expand/Collapse Branch Locations */}
            {group.branches.length > 0 && (
              <button
                onClick={() => toggleOrg(group.organizationName)}
                className="mt-4 flex w-full items-center justify-center gap-2 rounded-lg bg-white/40 px-3 py-2 text-sm font-medium text-foreground transition hover:bg-white/60 dark:bg-black/20 dark:hover:bg-black/40"
              >
                {expandedOrgs.has(group.organizationName) ? (
                  <>
                    <ChevronUp className="size-4" />
                    Hide {group.branches.length} branch location{group.branches.length !== 1 ? "s" : ""}
                  </>
                ) : (
                  <>
                    <ChevronDown className="size-4" />
                    Show {group.branches.length} branch location{group.branches.length !== 1 ? "s" : ""}
                  </>
                )}
              </button>
            )}
          </div>

          {/* Branch Location Cards */}
          {expandedOrgs.has(group.organizationName) && group.branches.length > 0 && (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {group.branches.map((branch) => (
                <div
                  key={branch.id}
                  className="rounded-lg border border-border/50 bg-card/50 p-4 transition hover:border-border/80 hover:bg-card/80"
                >
                  <h4 className="font-semibold text-foreground">{branch.canonical_name}</h4>

                  <div className="mt-2 space-y-1 text-xs text-muted-foreground">
                    {branch.formatted_address && (
                      <div className="flex gap-2">
                        <MapPin className="size-3 mt-0.5 flex-shrink-0" />
                        <span>{branch.formatted_address}</span>
                      </div>
                    )}
                    {branch.international_phone_number && (
                      <div className="flex gap-2">
                        <Phone className="size-3 flex-shrink-0" />
                        <a href={`tel:${branch.international_phone_number}`} className="hover:text-foreground">
                          {branch.international_phone_number}
                        </a>
                      </div>
                    )}
                  </div>

                  {branch.care_setting && (
                    <div className="mt-2 pt-2 border-t border-border/50">
                      <span
                        className={cn(
                          "text-[10px] font-semibold uppercase",
                          branch.care_setting === "residential"
                            ? "text-emerald-600 dark:text-emerald-400"
                            : "text-amber-600 dark:text-amber-400"
                        )}
                      >
                        {branch.care_setting === "residential" ? "⭐ Inpatient" : "Outpatient"}
                      </span>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
