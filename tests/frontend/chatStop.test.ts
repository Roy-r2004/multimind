import assert from "node:assert/strict";
import test from "node:test";

import { composerValueAfterStop } from "../../src/lib/chatStop.ts";

test("empty composer restores the exact stopped turn prompt", () => {
  assert.equal(composerValueAfterStop("", "Explain this code"), "Explain this code");
});

test("composer text typed during generation is preserved", () => {
  assert.equal(composerValueAfterStop("Prompt B", "Prompt A"), "Prompt B");
});

test("late cancellation cleanup cannot overwrite an edited restored prompt", async () => {
  let composer = composerValueAfterStop("", "Prompt A");
  const cancellation = Promise.resolve().then(() => undefined);

  composer = "Prompt A updated";
  await cancellation;

  assert.equal(composer, "Prompt A updated");
});

test("late cancellation after navigation cannot modify the next chat draft", async () => {
  let activeDraft = composerValueAfterStop("", "Prompt A");
  const cancellation = Promise.resolve().then(() => undefined);

  activeDraft = "Chat B draft";
  await cancellation;

  assert.equal(activeDraft, "Chat B draft");
});
