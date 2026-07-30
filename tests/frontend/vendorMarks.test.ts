import assert from "node:assert/strict";
import test from "node:test";

import { normalizeVendorKey, resolveVendorMarkId } from "../../src/lib/vendorMarks.ts";

test("normalizeVendorKey lowercases and strips spaces", () => {
  assert.equal(normalizeVendorKey("  Open AI "), "openai");
  assert.equal(normalizeVendorKey("xAI"), "xai");
});

test("resolveVendorMarkId maps catalog vendors and aliases", () => {
  assert.equal(resolveVendorMarkId("OpenAI"), "openai");
  assert.equal(resolveVendorMarkId("Anthropic"), "anthropic");
  assert.equal(resolveVendorMarkId("Claude"), "anthropic");
  assert.equal(resolveVendorMarkId("Google"), "google");
  assert.equal(resolveVendorMarkId("Gemini"), "google");
  assert.equal(resolveVendorMarkId("xAI"), "xai");
  assert.equal(resolveVendorMarkId("Grok"), "xai");
  assert.equal(resolveVendorMarkId("x.ai"), "xai");
  assert.equal(resolveVendorMarkId("DeepSeek"), "deepseek");
  assert.equal(resolveVendorMarkId("Mistral"), "mistral");
  assert.equal(resolveVendorMarkId("Mistralai"), "mistral");
  assert.equal(resolveVendorMarkId("Meta"), "meta");
  assert.equal(resolveVendorMarkId("Llama"), "meta");
  assert.equal(resolveVendorMarkId("Alibaba"), "alibaba");
  assert.equal(resolveVendorMarkId("Qwen"), "alibaba");
  assert.equal(resolveVendorMarkId("ChatGPT"), "openai");
});

test("resolveVendorMarkId falls back for unknown vendors", () => {
  assert.equal(resolveVendorMarkId("TotallyFakeAI"), "default");
  assert.equal(resolveVendorMarkId(""), "default");
});
