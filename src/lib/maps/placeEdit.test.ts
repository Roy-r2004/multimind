import { describe, expect, it } from "vitest";
import { placeToExportRow } from "@/lib/maps/exportDisplay";
import {
  draftFromPlace,
  parseBedCountInput,
  parseMaxBedsInput,
  parseCommaSeparated,
  payloadFromDraft,
} from "@/lib/maps/placeEdit";
import type { MapsPlaceItem } from "@/lib/maps/types";

const basePlace: MapsPlaceItem = {
  id: "1",
  google_place_id: "g1",
  canonical_name: "Example Rehab",
  place_types: [],
  formatted_address: "1 Main St, Helsinki",
  city_name: "Helsinki",
  region_name: null,
  latitude: null,
  longitude: null,
  international_phone_number: "+358 123",
  contact_email: null,
  raw_website: null,
  official_website: "example.com",
  website_source: "search",
  is_relevant: true,
  relevance_reason: null,
  confidence_score: 0.95,
  discovered_via_query: null,
  has_photo: false,
  verification_tier: "verified",
  export_eligible: true,
  enrichment_status: "completed",
  addictions_treated: ["Alcohol"],
  languages_spoken: [],
  treatment_price: null,
  bed_count: null,
  max_beds: null,
  verification_verdict: null,
  verification_reason: null,
  verification_source_url: null,
  keep_drop_decision: "keep",
  manually_excluded: false,
};

describe("place edit draft helpers", () => {
  it("uses raw MapsPlaceItem values instead of display placeholders", () => {
    const draft = draftFromPlace(basePlace);
    expect(draft.bed_count).toBe("");
    expect(draft.max_beds).toBe("");
    expect(draft.treatment_price).toBe("");
    expect(draft.contact_email).toBe("");
    expect(draft.addictions_treated).toBe("Alcohol");
    expect(draft.languages_spoken).toBe("");
    expect(draft.canonical_name).toBe("Example Rehab");
  });

  it("parses comma-separated addictions and languages", () => {
    expect(parseCommaSeparated("Alcohol, Opioids,  ")).toEqual(["Alcohol", "Opioids"]);
    expect(parseCommaSeparated("Not Specified")).toEqual([]);
    expect(parseCommaSeparated("")).toEqual([]);
  });

  it("parses bed count empty as null and rejects negatives", () => {
    expect(parseBedCountInput("")).toBeNull();
    expect(parseBedCountInput("8")).toBe(8);
    expect(parseBedCountInput("Not Specified")).toBeNull();
    expect(() => parseBedCountInput("-1")).toThrow();
  });

  it("parses max_beds empty as null and rejects negatives", () => {
    expect(parseMaxBedsInput("")).toBeNull();
    expect(parseMaxBedsInput("20")).toBe(20);
    expect(parseMaxBedsInput("Not Specified")).toBeNull();
    expect(() => parseMaxBedsInput("-1")).toThrow();
  });

  it("builds a PATCH payload of only changed fields", () => {
    const payload = payloadFromDraft(basePlace, {
      ...draftFromPlace(basePlace),
      bed_count: "8",
      contact_email: "desk@example.fi",
    });
    expect(payload).toEqual({
      bed_count: 8,
      contact_email: "desk@example.fi",
    });
  });

  it("sets and clears max_beds independently of bed_count", () => {
    const withMaxBeds = payloadFromDraft(basePlace, {
      ...draftFromPlace(basePlace),
      max_beds: "20",
    });
    expect(withMaxBeds).toEqual({ max_beds: 20 });

    const place = { ...basePlace, max_beds: 20, bed_count: 5 };
    const cleared = payloadFromDraft(place, {
      ...draftFromPlace(place),
      max_beds: "",
    });
    expect(cleared).toEqual({ max_beds: null });
  });

  it("does not send display placeholders as stored values", () => {
    const payload = payloadFromDraft(
      { ...basePlace, contact_email: "old@example.fi", treatment_price: "€100" },
      {
        ...draftFromPlace(basePlace),
        contact_email: "Not Specified",
        treatment_price: "Contact for pricing",
      },
    );
    expect(payload.contact_email).toBeNull();
    expect(payload.treatment_price).toBeNull();
  });
});

describe("placeToExportRow display is unchanged", () => {
  it("still shows Not Specified for null bed count", () => {
    const row = placeToExportRow(basePlace, "Finland");
    expect(row.cells["Bed Count"]).toBe("Not Specified");
    expect(row.cells["Treatment Price"]).toBe("Contact for pricing");
  });

  it("shows a persisted bed count", () => {
    const row = placeToExportRow({ ...basePlace, bed_count: 8 });
    expect(row.cells["Bed Count"]).toBe("8");
  });

  it("shows a persisted max_beds value", () => {
    const row = placeToExportRow({ ...basePlace, max_beds: 20 });
    expect(row.cells["max_beds"]).toBe("20");
  });
});
