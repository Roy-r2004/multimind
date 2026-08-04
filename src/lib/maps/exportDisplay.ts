import type { MapsPlaceItem } from "@/lib/maps/types";

export const EXPORT_COLUMNS = [
  "Facility Name",
  "Addictions Treated",
  "Location",
  "Languages Spoken",
  "Website",
  "Phone Number",
  "Treatment Price",
] as const;

const NOT_SPECIFIED = "Not Specified";
const CONTACT_PRICING = "Contact for pricing";

export type ExportRow = {
  place: MapsPlaceItem;
  cells: Record<(typeof EXPORT_COLUMNS)[number], string>;
};

function formatWebsite(place: MapsPlaceItem): string {
  const raw = (place.official_website || place.raw_website || "").trim();
  if (!raw) return NOT_SPECIFIED;
  if (raw.toLowerCase().startsWith("http://") || raw.toLowerCase().startsWith("https://")) {
    return raw;
  }
  return `https://${raw}`;
}

function formatLocation(place: MapsPlaceItem, countryName?: string): string {
  const address = (place.formatted_address || "").trim();
  if (address) return address;
  const parts = [place.city_name, place.region_name, countryName].filter(Boolean);
  return parts.length ? parts.join(", ") : NOT_SPECIFIED;
}

function joinList(values: string[] | null | undefined): string {
  return (values ?? [])
    .map((item) => item.trim())
    .filter(Boolean)
    .join(", ");
}

export function placeToExportRow(place: MapsPlaceItem, countryName?: string): ExportRow {
  const addictions = joinList(place.addictions_treated);
  const languages = joinList(place.languages_spoken);

  return {
    place,
    cells: {
      "Facility Name": place.canonical_name || NOT_SPECIFIED,
      "Addictions Treated": addictions || NOT_SPECIFIED,
      Location: formatLocation(place, countryName),
      "Languages Spoken": languages || NOT_SPECIFIED,
      Website: formatWebsite(place),
      "Phone Number": (place.international_phone_number || "").trim() || NOT_SPECIFIED,
      "Treatment Price": (place.treatment_price || "").trim() || CONTACT_PRICING,
    },
  };
}

export function sortPlacesForExport(places: MapsPlaceItem[]): MapsPlaceItem[] {
  return [...places].sort((a, b) =>
    a.canonical_name.localeCompare(b.canonical_name, undefined, { sensitivity: "base" }),
  );
}

export type ExportRowGroup = {
  organizationName: string;
  rows: ExportRow[];
};

/** Facilities sharing a name prefix before " - " (e.g. "Anton Proksch Institut - Baden")
 * are branches of the same organization. Groups rows so branches render together in
 * the same table instead of scattered across it. */
function organizationNameForPlace(place: MapsPlaceItem): string {
  return place.canonical_name.split(" - ")[0]?.trim() || place.canonical_name;
}

export function groupExportRowsByOrganization(rows: ExportRow[]): ExportRowGroup[] {
  const order: string[] = [];
  const groups = new Map<string, ExportRow[]>();

  rows.forEach((row) => {
    const orgName = organizationNameForPlace(row.place);
    if (!groups.has(orgName)) {
      groups.set(orgName, []);
      order.push(orgName);
    }
    groups.get(orgName)!.push(row);
  });

  return order.map((organizationName) => ({
    organizationName,
    rows: groups.get(organizationName)!,
  }));
}
