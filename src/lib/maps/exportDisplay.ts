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
