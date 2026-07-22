import assert from "node:assert/strict";
import test from "node:test";

import { buildTranscriptionFormData } from "../../src/lib/api/index.ts";
import { TRANSCRIPTION_LANGUAGE_OPTIONS } from "../../src/lib/transcriptionLanguages.ts";

test("transcription language options include English and French", () => {
  const labels = TRANSCRIPTION_LANGUAGE_OPTIONS.map((option) => option.label);

  assert.equal(labels.includes("English"), true);
  assert.equal(labels.includes("Français"), true);
});

test("transcription language options do not include Arabic", () => {
  const values: string[] = TRANSCRIPTION_LANGUAGE_OPTIONS.map((option) => option.value);

  assert.equal(values.includes("ar"), false);
});

test("auto transcription option is retained for English and French detection", () => {
  assert.deepEqual(TRANSCRIPTION_LANGUAGE_OPTIONS[0], {
    value: "auto",
    label: "Auto: English/French",
  });
});

test("selected transcription language is sent in form data", () => {
  const formData = buildTranscriptionFormData({
    file: new Blob(["audio"], { type: "audio/webm" }),
    language: "fr",
  });

  assert.equal(formData.get("language"), "fr");
});

test("default transcription language is auto", () => {
  const formData = buildTranscriptionFormData({
    file: new Blob(["audio"], { type: "audio/webm" }),
  });

  assert.equal(formData.get("language"), "auto");
});
