import assert from "node:assert/strict";
import test from "node:test";
import {
  mapsAdminCancelPath,
  mapsAdminCellsPath,
  mapsAdminDashboardPath,
  mapsAdminExportSummaryPath,
  mapsAdminPausePath,
  mapsAdminPlaceDetailPath,
  mapsAdminPlaceReviewPath,
  mapsAdminPlacesPath,
  mapsAdminRegionsPath,
  mapsAdminRetryEnrichmentPath,
  mapsAdminRetryFailedCellsPath,
  mapsAdminRetryWebsitesPath,
  mapsAdminResumePath,
} from "./adminPaths.ts";

const RUN_ID = "run-abc-123";
const PLACE_ID = "place-xyz-456";

test("mapsAdminDashboardPath targets the admin dashboard endpoint", () => {
  assert.equal(mapsAdminDashboardPath(RUN_ID), `/maps/runs/${RUN_ID}/dashboard`);
});

test("mapsAdminRegionsPath includes pagination query params", () => {
  assert.equal(
    mapsAdminRegionsPath(RUN_ID, 25, 50),
    `/maps/runs/${RUN_ID}/regions?limit=25&offset=50`,
  );
});

test("mapsAdminCellsPath serializes cell filters", () => {
  const path = mapsAdminCellsPath(RUN_ID, {
    status: "failed",
    region: "Île-de-France",
    capped_only: true,
    limit: 100,
    offset: 10,
  });
  assert.equal(
    path,
    `/maps/runs/${RUN_ID}/cells/paged?status=failed&region=%C3%8Ele-de-France&capped_only=true&limit=100&offset=10`,
  );
});

test("mapsAdminPlacesPath serializes provider filters", () => {
  const path = mapsAdminPlacesPath(RUN_ID, {
    search: "Alpha",
    client_eligibility: "eligible",
    lifecycle_status: "confirmed_public",
    limit: 20,
    offset: 0,
  });
  assert.equal(
    path,
    `/maps/runs/${RUN_ID}/places/paged?search=Alpha&client_eligibility=eligible&lifecycle_status=confirmed_public&limit=20&offset=0`,
  );
});

test("mapsAdminPlaceDetailPath and review path include place id", () => {
  assert.equal(mapsAdminPlaceDetailPath(RUN_ID, PLACE_ID), `/maps/runs/${RUN_ID}/places/${PLACE_ID}`);
  assert.equal(
    mapsAdminPlaceReviewPath(RUN_ID, PLACE_ID),
    `/maps/runs/${RUN_ID}/places/${PLACE_ID}/review`,
  );
});

test("campaign control paths target pause/resume/cancel/retry endpoints", () => {
  assert.equal(mapsAdminPausePath(RUN_ID), `/maps/runs/${RUN_ID}/pause`);
  assert.equal(mapsAdminResumePath(RUN_ID), `/maps/runs/${RUN_ID}/resume`);
  assert.equal(mapsAdminCancelPath(RUN_ID), `/maps/runs/${RUN_ID}/cancel`);
  assert.equal(mapsAdminRetryFailedCellsPath(RUN_ID), `/maps/runs/${RUN_ID}/retry-failed-cells`);
  assert.equal(mapsAdminRetryWebsitesPath(RUN_ID), `/maps/runs/${RUN_ID}/retry-websites`);
  assert.equal(mapsAdminRetryEnrichmentPath(RUN_ID), `/maps/runs/${RUN_ID}/retry-enrichment`);
});

test("mapsAdminExportSummaryPath targets export summary endpoint", () => {
  assert.equal(mapsAdminExportSummaryPath(RUN_ID), `/maps/runs/${RUN_ID}/export-summary`);
});
