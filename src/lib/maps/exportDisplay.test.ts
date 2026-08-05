import { describe, expect, it } from "vitest";
import { placeToExportRow } from "@/lib/maps/exportDisplay";
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
  verification_verdict: null,
  verification_reason: null,
  verification_source_url: null,
  keep_drop_decision: null,
  manually_excluded: false,
};

describe("placeToExportRow", () => {
  it("formats export cells like the CSV schema", () => {
    const row = placeToExportRow(basePlace, "Finland");
    expect(row.cells["Facility Name"]).toBe("Example Rehab");
    expect(row.cells["Addictions Treated"]).toBe("Alcohol");
    expect(row.cells.Location).toBe("1 Main St, Helsinki");
    expect(row.cells["Languages Spoken"]).toBe("Not Specified");
    expect(row.cells.Website).toBe("https://example.com");
    expect(row.cells["Phone Number"]).toBe("+358 123");
    expect(row.cells["Treatment Price"]).toBe("Contact for pricing");
  });

  it("fills every export column when enrichment fields are missing", () => {
    const row = placeToExportRow(
      {
        ...basePlace,
        addictions_treated: undefined as unknown as string[],
        languages_spoken: undefined as unknown as string[],
        treatment_price: null,
        official_website: null,
        raw_website: null,
        international_phone_number: null,
        formatted_address: null,
        city_name: null,
        region_name: null,
      },
      "Finland",
    );

    for (const column of [
      "Facility Name",
      "Addictions Treated",
      "Location",
      "Languages Spoken",
      "Website",
      "Phone Number",
      "Treatment Price",
    ] as const) {
      expect(row.cells[column].length).toBeGreaterThan(0);
    }
    expect(row.cells["Addictions Treated"]).toBe("Not Specified");
    expect(row.cells["Languages Spoken"]).toBe("Not Specified");
    expect(row.cells["Treatment Price"]).toBe("Contact for pricing");
  });
});
