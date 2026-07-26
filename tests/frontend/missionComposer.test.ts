import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";

const source = readFileSync(
  resolve(import.meta.dirname, "../../src/components/scraping/MissionComposer.tsx"),
  "utf8",
);

test("new mission exposes only title, country, and blueprint generation", () => {
  assert.match(source, /Mission Title/);
  assert.match(source, /CountrySelector/);
  assert.match(source, /Generate Blueprint/);
  assert.doesNotMatch(source, /Model Set/);
  assert.doesNotMatch(source, /Project/);
  assert.doesNotMatch(source, /model_set_id/);
});
