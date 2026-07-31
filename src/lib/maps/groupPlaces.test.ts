import assert from "node:assert/strict";
import test from "node:test";
import { groupVerifiedPlaces } from "./groupPlaces.ts";
import type { MapsPlaceItem } from "./types";

function place(id: string, name: string, website: string | null): MapsPlaceItem {
  return {
    id,
    google_place_id: id,
    canonical_name: name,
    place_types: [],
    formatted_address: null,
    city_name: null,
    region_name: null,
    latitude: null,
    longitude: null,
    international_phone_number: null,
    raw_website: website,
    official_website: website,
    website_source: website ? "search" : null,
    is_relevant: true,
    relevance_reason: null,
    confidence_score: null,
    discovered_via_query: null,
    has_photo: false,
  };
}

test("groups same-name locations only when their verified website host matches", () => {
  const groups = groupVerifiedPlaces([
    place("1", "Centre Alpha", "https://alpha.by/minsk"),
    place("2", "Centre Alpha", "https://www.alpha.by/brest"),
  ]);

  assert.equal(groups.length, 1);
  assert.equal(groups[0].places.length, 2);
});

test("keeps generic same-name facilities separate when website hosts differ", () => {
  const groups = groupVerifiedPlaces([
    place("1", "Dispanser Psihonevrologicheskii", "https://borcrb.by/"),
    place("2", "Dispanser Psihonevrologicheskii", "https://mcgp.by/"),
  ]);

  assert.equal(groups.length, 2);
});

test("excludes places without a verified official website", () => {
  const groups = groupVerifiedPlaces([
    place("1", "Verified Centre", "https://verified.by/"),
    place("2", "Unverified Centre", null),
  ]);

  assert.deepEqual(
    groups.map((group) => group.name),
    ["Verified Centre"],
  );
});
