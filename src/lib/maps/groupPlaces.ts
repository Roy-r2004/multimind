import type { MapsPlaceItem } from "./types";

export interface PlaceGroup {
  key: string;
  name: string;
  websiteHost: string;
  places: MapsPlaceItem[];
}

function normalizedWebsiteHost(url: string): string | null {
  try {
    return new URL(url).hostname.toLowerCase().replace(/^www\./, "");
  } catch {
    return null;
  }
}

export function groupVerifiedPlaces(places: MapsPlaceItem[]): PlaceGroup[] {
  const groups = new Map<string, PlaceGroup>();
  for (const place of places) {
    if (!place.official_website) continue;
    const websiteHost = normalizedWebsiteHost(place.official_website);
    if (!websiteHost) continue;
    const nameKey = place.canonical_name.trim().toLowerCase();
    const key = `${nameKey}::${websiteHost}`;
    const existing = groups.get(key);
    if (existing) {
      existing.places.push(place);
    } else {
      groups.set(key, {
        key,
        name: place.canonical_name,
        websiteHost,
        places: [place],
      });
    }
  }
  return Array.from(groups.values());
}
