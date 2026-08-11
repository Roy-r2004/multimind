import assert from "node:assert/strict";
import test from "node:test";

import {
  libraryFileContentView,
  libraryItemOpensDetail,
} from "../../src/lib/libraryContent.ts";

test("libraryItemOpensDetail allows documents and files", () => {
  assert.equal(libraryItemOpensDetail("document"), true);
  assert.equal(libraryItemOpensDetail("file"), true);
  assert.equal(libraryItemOpensDetail("other"), false);
  assert.equal(libraryItemOpensDetail(null), false);
});

test("libraryFileContentView renders ready extract text", () => {
  const view = libraryFileContentView({
    excerpt_status: "ready",
    text_excerpt: "Line one\nLine two",
  });
  assert.deepEqual(view, { kind: "text", text: "Line one\nLine two" });
});

test("libraryFileContentView handles empty extract", () => {
  const view = libraryFileContentView({
    excerpt_status: "empty",
    text_excerpt: null,
  });
  assert.equal(view.kind, "message");
  assert.match(view.message, /No readable text/);
});

test("libraryFileContentView handles failed extract", () => {
  const view = libraryFileContentView({
    excerpt_status: "failed",
    text_excerpt: null,
  });
  assert.equal(view.kind, "message");
  assert.match(view.message, /could not extract/);
});

test("libraryFileContentView handles image status without breaking", () => {
  const view = libraryFileContentView({
    excerpt_status: "image",
    text_excerpt: null,
  });
  assert.equal(view.kind, "message");
  assert.match(view.message, /Preview is not available/);
});

test("libraryFileContentView treats ready-with-blank-excerpt as empty", () => {
  const view = libraryFileContentView({
    excerpt_status: "ready",
    text_excerpt: "   ",
  });
  assert.equal(view.kind, "message");
  assert.match(view.message, /No readable text/);
});
