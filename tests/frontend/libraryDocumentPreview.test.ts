import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import {
  LIBRARY_DOCUMENT_DEFAULT_MODE,
  libraryDocumentPreviewContent,
} from "../../src/lib/libraryDocumentPreview.ts";

const root = join(dirname(fileURLToPath(import.meta.url)), "../..");
const editorSrc = readFileSync(
  join(root, "src/components/library/LibraryDocumentEditor.tsx"),
  "utf8",
);
const itemSrc = readFileSync(join(root, "src/routes/library.$itemId.tsx"), "utf8");
const messageContentSrc = readFileSync(
  join(root, "src/components/chat/MessageContent.tsx"),
  "utf8",
);

const TABLE_MARKDOWN = `| Facility Type | Estimated Facilities | Source | Notes |
| --- | --- | --- | --- |
| Inpatient Rehab | ~2,000–2,500 | SAMHSA | Residential programs |`;

test("default Library document mode is Edit", () => {
  assert.equal(LIBRARY_DOCUMENT_DEFAULT_MODE, "edit");
  assert.match(editorSrc, /useState<LibraryDocumentMode>\(LIBRARY_DOCUMENT_DEFAULT_MODE\)/);
});

test("Preview renders live unsaved content, not the last saved snapshot", () => {
  assert.equal(libraryDocumentPreviewContent("unsaved draft"), "unsaved draft");
  assert.notEqual(libraryDocumentPreviewContent("unsaved draft"), "saved from db");
  assert.match(editorSrc, /libraryDocumentPreviewContent\(value\)/);
  assert.match(editorSrc, /<MessageContent>\{previewText\}<\/MessageContent>/);
  assert.match(itemSrc, /value=\{content\}/);
  assert.match(
    itemSrc,
    /onChange=\{\(next\) => \{\s*setContent\(next\);\s*setDirty\(true\);/,
  );
});

test("Preview uses MessageContent table markup instead of raw pipes", () => {
  assert.match(editorSrc, /data-testid="library-document-preview"/);
  assert.match(messageContentSrc, /table: \(\{ children \}\) =>/);
  assert.match(messageContentSrc, /thead: \(\{ children \}\) =>/);
  assert.match(messageContentSrc, /<th /);
  assert.match(messageContentSrc, /<td /);
  assert.match(messageContentSrc, /overflow-x-auto rounded-lg border/);
  assert.match(messageContentSrc, /min-w-\[36rem\] table-auto/);
  assert.match(editorSrc, /min-w-0 overflow-x-auto/);
  assert.doesNotMatch(
    editorSrc.slice(editorSrc.indexOf("library-document-preview")),
    /<pre[^>]*>\{previewText\}/,
  );
  assert.match(TABLE_MARKDOWN, /\| Facility Type \|/);
  assert.match(editorSrc, /MessageContent>\{\s*previewText\s*\}<\/MessageContent>/);
});

test("switching Preview back to Edit does not mutate Markdown or call onChange", () => {
  const original = TABLE_MARKDOWN;
  assert.equal(libraryDocumentPreviewContent(original), original);
  assert.match(editorSrc, /onClick=\{\(\) => setMode\("edit"\)\}/);
  assert.match(editorSrc, /onClick=\{\(\) => setMode\("preview"\)\}/);
  const setModeBlock = editorSrc.slice(
    editorSrc.indexOf('onClick={() => setMode("preview")}'),
    editorSrc.indexOf('onClick={() => setMode("preview")}') + 80,
  );
  assert.doesNotMatch(setModeBlock, /onChange/);
  assert.doesNotMatch(editorSrc, /onClick=\{\(\) => setMode\("preview"\); onChange/);
});

test("Preview does not clear dirty state or trigger Save", () => {
  assert.doesNotMatch(editorSrc, /setDirty/);
  assert.doesNotMatch(editorSrc, /api\.library/);
  assert.doesNotMatch(editorSrc, /\.save\(/);
  assert.match(itemSrc, /disabled=\{saving \|\| !dirty\}/);
  assert.match(itemSrc, /\{dirty \? "Unsaved changes" : "All changes saved"\}/);
  const previewToggle = editorSrc.includes('setMode("preview")');
  const saveInEditor = editorSrc.includes("updateItem");
  assert.equal(previewToggle, true);
  assert.equal(saveInEditor, false);
});

test("existing editor toolbar, paste, and textarea remain in Edit mode", () => {
  assert.match(editorSrc, /onPaste=\{onPaste\}/);
  assert.match(editorSrc, /applyLibraryTablePaste\(/);
  assert.match(editorSrc, /mode === "edit" \? \(/);
  assert.match(editorSrc, /<textarea/);
  assert.match(editorSrc, /formatLibraryDocumentInline/);
  assert.match(editorSrc, /handleLibraryDocumentListKey/);
});
