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

test("the generic default mode (used when no initialMode is passed) is Edit", () => {
  assert.equal(LIBRARY_DOCUMENT_DEFAULT_MODE, "edit");
  assert.match(
    editorSrc,
    /useState<LibraryDocumentMode>\(\s*initialMode \?\? LIBRARY_DOCUMENT_DEFAULT_MODE,?\s*\)/,
  );
});

test("the full document page opens in Preview mode, not Edit", () => {
  // Requirement: every time a user opens a full document page, the initial
  // mode must be Preview — wired via the editor's initialMode prop.
  assert.match(itemSrc, /<LibraryDocumentEditor[\s\S]{0,400}initialMode="preview"/);
});

test("the Create Document dialog keeps the generic (Edit) default — unaffected by the item page override", () => {
  const indexSrc = readFileSync(join(root, "src/routes/library.index.tsx"), "utf8");
  const createEditorBlock = indexSrc.slice(
    indexSrc.indexOf("library-create-body"),
    indexSrc.indexOf("library-create-body") + 400,
  );
  assert.doesNotMatch(createEditorBlock, /initialMode/);
});

test("Preview renders live unsaved content, not the last saved snapshot", () => {
  assert.equal(libraryDocumentPreviewContent("unsaved draft"), "unsaved draft");
  assert.notEqual(libraryDocumentPreviewContent("unsaved draft"), "saved from db");
  assert.match(editorSrc, /libraryDocumentPreviewContent\(value\)/);
  assert.match(
    editorSrc,
    /<MessageContent variant="verdict" showTableCopy=\{false\}>\s*\{previewText\}\s*<\/MessageContent>/,
  );
  assert.match(itemSrc, /value=\{content\}/);
  assert.match(itemSrc, /onChange=\{\(next\) => \{\s*setContent\(next\);\s*markDirty\(\);/);
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
  assert.match(editorSrc, /MessageContent[^>]*>\s*\{\s*previewText\s*\}\s*<\/MessageContent>/);
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

test("opening in Preview still shows the current (possibly unsaved) draft, not a blank/empty view", () => {
  // Since `mode` only ever controls which renderer is shown, and the editor
  // never substitutes a different value for Preview, a document that loads
  // with unsaved local edits (or any content at all) is fully visible on
  // first render, even though that first render is Preview.
  assert.equal(libraryDocumentPreviewContent("loaded draft text"), "loaded draft text");
  assert.doesNotMatch(editorSrc, /mode === "preview".*value\s*=\s*""/);
});

test("switching modes never discards or alters the draft: value/onChange are independent of mode", () => {
  // The textarea (Edit) and the preview renderer (Preview) are fed from the
  // exact same `value` prop and the same `onChange` callback lives only on
  // the textarea — toggling `mode` itself never calls onChange or mutates
  // `value`, so flipping Edit -> Preview -> Edit round-trips the same draft.
  assert.match(editorSrc, /<textarea[\s\S]{0,120}value=\{value\}/);
  assert.match(editorSrc, /libraryDocumentPreviewContent\(value\)/);
  assert.doesNotMatch(editorSrc, /setMode\([^)]*\)[^}]*onChange/);
  assert.doesNotMatch(editorSrc, /onChange[^}]*setMode\(/);
});

test("a successful save never forces a mode change — Edit stays Edit after Save", () => {
  // save() in the item page only touches title/content/folder/labels/dirty;
  // it must never call setMode, and the editor itself never resets mode as
  // a side effect of a value/prop change (e.g. after save re-syncs `value`).
  assert.doesNotMatch(itemSrc, /setMode/);
  assert.doesNotMatch(editorSrc, /useEffect\([^)]*setMode/);
});
