import type { MapsPlaceItem, MapsPlaceUpdateInput } from "@/lib/maps/types";

const DISPLAY_PLACEHOLDERS = new Set(["Not Specified", "Contact for pricing"]);

export type PlaceEditDraft = {
  canonical_name: string;
  addictions_treated: string;
  formatted_address: string;
  languages_spoken: string;
  official_website: string;
  contact_email: string;
  international_phone_number: string;
  treatment_price: string;
  bed_count: string;
};

export type MapsPlaceUpdatePayload = MapsPlaceUpdateInput;

export function draftFromPlace(place: MapsPlaceItem): PlaceEditDraft {
  return {
    canonical_name: place.canonical_name ?? "",
    addictions_treated: joinCommaList(place.addictions_treated),
    formatted_address: place.formatted_address ?? "",
    languages_spoken: joinCommaList(place.languages_spoken),
    official_website: place.official_website || place.raw_website || "",
    contact_email: place.contact_email ?? "",
    international_phone_number: place.international_phone_number ?? "",
    treatment_price: place.treatment_price ?? "",
    bed_count: place.bed_count != null ? String(place.bed_count) : "",
  };
}

export function joinCommaList(values: string[] | null | undefined): string {
  return (values ?? [])
    .map((item) => item.trim())
    .filter(Boolean)
    .join(", ");
}

export function parseCommaSeparated(raw: string): string[] {
  return raw
    .split(",")
    .map((item) => stripPlaceholder(item.trim()))
    .filter((item): item is string => Boolean(item));
}

export function parseBedCountInput(raw: string): number | null {
  const trimmed = stripPlaceholder(raw.trim());
  if (!trimmed) return null;
  if (!/^\d+$/.test(trimmed)) {
    throw new Error("Bed count must be a whole number of 0 or more");
  }
  return Number(trimmed);
}

export function payloadFromDraft(
  original: MapsPlaceItem,
  draft: PlaceEditDraft,
): MapsPlaceUpdatePayload {
  const payload: MapsPlaceUpdatePayload = {};
  const name = (draft.canonical_name ?? "").trim();
  if (!name) {
    throw new Error("Facility name is required");
  }
  if (name !== (original.canonical_name ?? "").trim()) {
    payload.canonical_name = name;
  }

  const addictions = parseCommaSeparated(draft.addictions_treated);
  if (!sameList(addictions, original.addictions_treated ?? [])) {
    payload.addictions_treated = addictions;
  }

  const address = emptyToNull(draft.formatted_address);
  if (address !== emptyToNull(original.formatted_address ?? "")) {
    payload.formatted_address = address;
  }

  const languages = parseCommaSeparated(draft.languages_spoken);
  if (!sameList(languages, original.languages_spoken ?? [])) {
    payload.languages_spoken = languages;
  }

  const website = emptyToNull(draft.official_website);
  const originalWebsite = emptyToNull(original.official_website || original.raw_website || "");
  if (website !== originalWebsite) {
    payload.official_website = website;
  }

  const email = emptyToNull(draft.contact_email);
  if (email !== emptyToNull(original.contact_email ?? "")) {
    payload.contact_email = email;
  }

  const phone = emptyToNull(draft.international_phone_number);
  if (phone !== emptyToNull(original.international_phone_number ?? "")) {
    payload.international_phone_number = phone;
  }

  const price = emptyToNull(draft.treatment_price);
  if (price !== emptyToNull(original.treatment_price ?? "")) {
    payload.treatment_price = price;
  }

  const bedCount = parseBedCountInput(draft.bed_count);
  if (bedCount !== (original.bed_count ?? null)) {
    payload.bed_count = bedCount;
  }

  return payload;
}

function stripPlaceholder(value: string): string | null {
  if (!value || DISPLAY_PLACEHOLDERS.has(value)) return null;
  return value;
}

function emptyToNull(raw: string): string | null {
  return stripPlaceholder(raw.trim());
}

function sameList(left: string[], right: string[]): boolean {
  return left.length === right.length && left.every((item, index) => item === right[index]);
}
