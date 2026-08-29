import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(
  new URL("../../src/components/ModelSetModal.tsx", import.meta.url),
  "utf8",
);

test("Referee model sets expose editable Custom Verdict Instructions", () => {
  const refereeBranch = source
    .split('{strategy === "Referee" ? (', 2)[1]
    ?.split(") : (", 1)[0];

  assert.ok(refereeBranch);
  assert.match(refereeBranch, /Fixed Referee Prompt/);
  assert.match(refereeBranch, /readOnly/);
  assert.match(refereeBranch, /Custom Verdict Instructions/);
  assert.match(refereeBranch, /value=\{custom\}/);
  assert.match(refereeBranch, /onChange=\{\(e\) => setCustom\(e\.target\.value\)\}/);
});

test("saving still persists custom instructions on the Model Set", () => {
  assert.match(source, /customInstructions: custom\.trim\(\) \|\| undefined/);
});
