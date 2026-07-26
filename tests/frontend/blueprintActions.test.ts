import assert from "node:assert/strict";
import test from "node:test";
import {
  activeApprovedBlueprint,
  canApproveBlueprint,
  canDiscardBlueprint,
  canEditBlueprint,
  canRegenerateBlueprint,
  canRejectBlueprint,
  canRequestBlueprintChanges,
  parseStructuredBlueprint,
  scrapingCtaMessage,
  validRevisionInstruction,
} from "../../src/lib/scraping/blueprintActions";
import type { ScrapingBlueprint } from "../../src/lib/scraping/types";

function blueprint(
  id: string,
  version: number,
  status: ScrapingBlueprint["status"],
): ScrapingBlueprint {
  return {
    id,
    mission_id: "mission-1",
    version,
    display_name: null,
    status,
    model_set_id: "set-1",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

test("review-ready blueprint exposes review actions", () => {
  assert.equal(canApproveBlueprint("ready_for_review"), true);
  assert.equal(canRejectBlueprint("ready_for_review"), true);
  assert.equal(canEditBlueprint("ready_for_review"), true);
  assert.equal(canDiscardBlueprint("ready_for_review"), true);
  assert.equal(canRequestBlueprintChanges("ready_for_review"), true);
  assert.equal(canRegenerateBlueprint("ready_for_review"), true);
});

test("approved blueprint is immutable but can request a new version", () => {
  assert.equal(canEditBlueprint("approved"), false);
  assert.equal(canDiscardBlueprint("approved"), false);
  assert.equal(canRequestBlueprintChanges("approved"), true);
  assert.equal(canRegenerateBlueprint("approved"), true);
});

test("active generation cannot be regenerated or reviewed", () => {
  for (const status of ["queued", "running"] as const) {
    assert.equal(canRegenerateBlueprint(status), false);
    assert.equal(canApproveBlueprint(status), false);
    assert.equal(canRequestBlueprintChanges(status), false);
  }
});

test("failed blueprints can be revised or regenerated but not approved", () => {
  assert.equal(canRequestBlueprintChanges("failed"), true);
  assert.equal(canRegenerateBlueprint("failed"), true);
  assert.equal(canApproveBlueprint("failed"), false);
});

test("draft, rejected, and discarded versions expose only supported actions", () => {
  assert.equal(canEditBlueprint("draft"), true);
  assert.equal(canDiscardBlueprint("draft"), true);
  assert.equal(canApproveBlueprint("draft"), false);
  for (const status of ["rejected", "discarded"] as const) {
    assert.equal(canEditBlueprint(status), false);
    assert.equal(canDiscardBlueprint(status), false);
    assert.equal(canRequestBlueprintChanges(status), false);
  }
});

test("revision instructions must contain non-whitespace content", () => {
  assert.equal(validRevisionInstruction(""), false);
  assert.equal(validRevisionInstruction("   "), false);
  assert.equal(validRevisionInstruction("Add local terminology."), true);
});

test("structured blueprint JSON is validated without mutation", () => {
  assert.equal(parseStructuredBlueprint("{"), null);
  const parsed = parseStructuredBlueprint('{"country_dossier":{"country_name":"Austria"}}');
  assert.deepEqual(parsed, { country_dossier: { country_name: "Austria" } });
});

test("active approved blueprint can differ from latest version", () => {
  const approved = blueprint("approved", 1, "approved");
  const latest = blueprint("latest", 2, "ready_for_review");
  assert.equal(activeApprovedBlueprint([latest, approved], approved.id), approved);
  assert.equal(activeApprovedBlueprint([latest, approved], latest.id), undefined);
});

test("scraping CTA enables campaign starts after approval", () => {
  const approved = blueprint("approved", 1, "approved");
  assert.equal(scrapingCtaMessage(undefined), "Approval is required.");
  assert.equal(
    scrapingCtaMessage(approved),
    "Start a test campaign from this approved Country Blueprint.",
  );
});
