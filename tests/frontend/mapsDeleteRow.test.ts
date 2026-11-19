import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

test("delete facility uses the authenticated permanent row API", () => {
  const source = readFileSync("src/lib/maps/api.ts", "utf8");
  assert.match(source, /export function deleteMapsCensusPlace/);
  assert.match(source, /method: "DELETE"/);
  assert.match(source, /token: auth\.token[\s\S]*orgId: auth\.orgId/);
});

test("success refreshes and closes Edit while failure keeps the row", () => {
  const source = readFileSync("src/routes/maps.$runId.tsx", "utf8");
  assert.match(source, /await deleteMapsCensusPlace\(auth, runId, place\.id\)/);
  assert.match(source, /handleCancelEdit\(\);[\s\S]*setPollTick/);
  assert.match(source, /toast\.success/);
  assert.match(source, /phase1Places\.length === 1[\s\S]*setPhase1Offset/);
  assert.match(source, /phase2Places\.length === 1[\s\S]*setPhase2Offset/);
  assert.match(source, /catch \(err\)[\s\S]*toast\.error\(message\)/);
  assert.doesNotMatch(source, /catch \(err\)[\s\S]{0,160}setPhase[12]Places/);
});

test("edit UI confirms by facility name and cancellation only closes the dialog", () => {
  const source = readFileSync("src/routes/maps.$runId.tsx", "utf8");
  assert.match(source, /Delete \{deleteCandidate\?\.canonical_name\}\?/);
  assert.match(source, /Are you sure you want to permanently delete this facility\?/);
  assert.match(source, /<AlertDialogCancel[^>]*>Cancel<\/AlertDialogCancel>/);
  assert.match(source, /if \(!open && !deleteInFlightRef\.current\) setDeleteCandidate\(null\)/);
});

test("a synchronous in-flight guard prevents duplicate DELETE submissions", () => {
  const source = readFileSync("src/routes/maps.$runId.tsx", "utf8");
  assert.match(source, /if \(!auth \|\| !deleteCandidate \|\| deleteInFlightRef\.current\) return/);
  assert.match(source, /deleteInFlightRef\.current = true/);
  assert.match(source, /finally \{[\s\S]*deleteInFlightRef\.current = false/);
});
