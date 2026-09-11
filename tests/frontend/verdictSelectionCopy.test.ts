import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import {
  buildSelectionCopyPayload,
  filterWrapAncestors,
  handleVerdictSelectionCopy,
  htmlToCopyPlainText,
  isFormFieldCopyTarget,
  wrapInnerHtml,
} from "../../src/lib/verdictSelectionCopy.ts";

const root = join(dirname(fileURLToPath(import.meta.url)), "../..");
const utilSrc = readFileSync(join(root, "src/lib/verdictSelectionCopy.ts"), "utf8");
const rootSrc = readFileSync(join(root, "src/routes/__root.tsx"), "utf8");
const chatSrc = readFileSync(join(root, "src/routes/chat.tsx"), "utf8");
const copyButtonSrc = readFileSync(
  join(root, "src/components/chat/VerdictCopyButton.tsx"),
  "utf8",
);
const libraryEditorSrc = readFileSync(
  join(root, "src/components/library/LibraryDocumentEditor.tsx"),
  "utf8",
);

const NESTED_FRAGMENT = `You already have:
<ul>
  <li>FS (Final Segments): ~100 clinically coherent segments...</li>
  <li>RP-REHAB: ~20 core archetypes...</li>
  <li>Rehab Resource Vectors: quantified resource footprints...</li>
</ul>`;

test("selecting normal paragraph text keeps a paragraph wrapper", () => {
  const payload = buildSelectionCopyPayload("Hello world", [{ tag: "p" }]);
  assert.ok(payload);
  assert.equal(payload.html, "<p>Hello world</p>");
  assert.equal(payload.plainText, "Hello world");
});

test("selecting one bullet item restores ul/li wrappers", () => {
  const payload = buildSelectionCopyPayload("FS (Final Segments): ~100", [
    { tag: "li" },
    { tag: "ul" },
  ]);
  assert.ok(payload);
  assert.equal(payload.html, "<ul><li>FS (Final Segments): ~100</li></ul>");
  assert.equal(payload.plainText, "• FS (Final Segments): ~100");
});

test("selecting multiple bullet items wraps the cloned lis in a ul", () => {
  const inner =
    "<li>FS (Final Segments): ~100 clinically coherent segments...</li>" +
    "<li>RP-REHAB: ~20 core archetypes...</li>" +
    "<li>Rehab Resource Vectors: quantified resource footprints...</li>";
  const payload = buildSelectionCopyPayload(inner, [{ tag: "ul" }]);
  assert.ok(payload);
  assert.match(payload.html, /^<ul><li>/);
  assert.match(payload.html, /<\/li><\/ul>$/);
  assert.equal(
    payload.plainText,
    [
      "• FS (Final Segments): ~100 clinically coherent segments...",
      "• RP-REHAB: ~20 core archetypes...",
      "• Rehab Resource Vectors: quantified resource footprints...",
    ].join("\n"),
  );
});

test("nested bullet lists keep parent and child markers plus indentation", () => {
  const payload = buildSelectionCopyPayload(NESTED_FRAGMENT, [{ tag: "li" }, { tag: "ul" }]);
  assert.ok(payload);
  assert.match(payload.html, /<ul><li>You already have:/);
  assert.match(payload.html, /<ul>\s*<li>FS \(Final Segments\)/);
  assert.equal(
    payload.plainText,
    [
      "• You already have:",
      "    • FS (Final Segments): ~100 clinically coherent segments...",
      "    • RP-REHAB: ~20 core archetypes...",
      "    • Rehab Resource Vectors: quantified resource footprints...",
    ].join("\n"),
  );
});

test("selection starting inside li text after the bullet still restores list semantics", () => {
  const ancestors = filterWrapAncestors([{ tag: "li" }, { tag: "ul" }]);
  assert.deepEqual(
    ancestors.map((a) => a.tag),
    ["li", "ul"],
  );
  const html = wrapInnerHtml("You already have:", ancestors);
  assert.equal(html, "<ul><li>You already have:</li></ul>");
  const payload = buildSelectionCopyPayload(NESTED_FRAGMENT, [{ tag: "li" }, { tag: "ul" }]);
  assert.ok(payload);
  assert.match(payload.plainText, /^• You already have:/);
  assert.doesNotMatch(payload.plainText, /^You already have:/);
});

test("ordered lists keep numbering when practical", () => {
  const payload = buildSelectionCopyPayload("<li>First</li><li>Second</li>", [{ tag: "ol" }]);
  assert.ok(payload);
  assert.equal(payload.html, "<ol><li>First</li><li>Second</li></ol>");
  assert.equal(payload.plainText, "1. First\n2. Second");
});

test("bold text inside selection is preserved in HTML", () => {
  const payload = buildSelectionCopyPayload("world", [{ tag: "strong" }, { tag: "p" }]);
  assert.ok(payload);
  assert.equal(payload.html, "<p><strong>world</strong></p>");
  assert.equal(payload.plainText, "world");
  assert.match(htmlToCopyPlainText("<p>Hello <strong>world</strong></p>"), /Hello world/);
});

test("links, tables, and nested list ancestors survive wrapping", () => {
  const withLink = buildSelectionCopyPayload("docs", [
    { tag: "a", href: "https://ex.test" },
    { tag: "p" },
  ]);
  assert.equal(withLink?.html, '<p><a href="https://ex.test">docs</a></p>');

  const table = buildSelectionCopyPayload("Alpha", [
    { tag: "td" },
    { tag: "tr" },
    { tag: "tbody" },
    { tag: "table" },
  ]);
  assert.ok(table);
  assert.match(table.html, /<table><tbody><tr><td>Alpha<\/td><\/tr><\/tbody><\/table>/);
  assert.equal(table.plainText, "Alpha");
  assert.equal(
    htmlToCopyPlainText("<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>"),
    "A | B\n1 | 2",
  );
});

test("outer li around an already-selected nested ul is not added", () => {
  const tags = filterWrapAncestors([{ tag: "ul" }, { tag: "li" }, { tag: "ul" }]);
  assert.deepEqual(
    tags.map((a) => a.tag),
    ["ul"],
  );
});

test("selection outside Verdict should use normal browser behavior", () => {
  const stored: Record<string, string> = {};
  const event = {
    defaultPrevented: false,
    preventDefault() {
      this.defaultPrevented = true;
    },
    clipboardData: {
      setData(type: string, value: string) {
        stored[type] = value;
      },
    },
    target: { tagName: "DIV", closest: () => null },
  } as unknown as ClipboardEvent;

  const handled = handleVerdictSelectionCopy(event);
  assert.equal(handled, false);
  assert.equal(event.defaultPrevented, false);
  assert.deepEqual(stored, {});
});

test("textarea/input copy should remain untouched", () => {
  const textarea = {
    tagName: "TEXTAREA",
    closest(selector: string) {
      return selector.includes("textarea") ? this : null;
    },
  };
  const input = {
    tagName: "INPUT",
    closest(selector: string) {
      return selector.includes("input") ? this : null;
    },
  };
  assert.equal(isFormFieldCopyTarget(textarea as unknown as Element), true);
  assert.equal(isFormFieldCopyTarget(input as unknown as Element), true);

  const stored: Record<string, string> = {};
  const event = {
    defaultPrevented: false,
    preventDefault() {
      this.defaultPrevented = true;
    },
    clipboardData: {
      setData(type: string, value: string) {
        stored[type] = value;
      },
    },
    target: textarea,
  } as unknown as ClipboardEvent;
  assert.equal(handleVerdictSelectionCopy(event), false);
  assert.equal(event.defaultPrevented, false);
  assert.deepEqual(stored, {});
});

test("copy event is intercepted on document, not inside the Copy button", () => {
  assert.match(rootSrc, /document\.addEventListener\("copy", onCopy\)/);
  assert.match(rootSrc, /handleVerdictSelectionCopy\(event\)/);
  assert.match(chatSrc, /data-verdict-copy-root=""/);
  assert.match(utilSrc, /event\.clipboardData/);
  assert.match(utilSrc, /setData\("text\/html"/);
  assert.match(utilSrc, /setData\("text\/plain"/);
  assert.match(utilSrc, /event\.preventDefault\(\)/);
  assert.doesNotMatch(copyButtonSrc, /handleVerdictSelectionCopy/);
});

test("full Verdict Copy still uses copyRichContent with Markdown plus body HTML", () => {
  assert.match(copyButtonSrc, /await copyRichContent\(\{ plainText: text, html: getHtml\?\.\(\), plainTextIsMarkdown: true \}\)/);
  assert.match(
    chatSrc,
    /<VerdictCopyButton\s+text=\{turn\.verdict\.text\}\s+getHtml=\{\(\) => getVerdictBodyCopyHtml\(verdictContentRef\.current\)\}/,
  );
});

test("Library paste behavior was not changed", () => {
  assert.match(libraryEditorSrc, /onPaste=\{onPaste\}/);
  assert.match(libraryEditorSrc, /applyLibraryTablePaste\(/);
  assert.match(libraryEditorSrc, /clipboard\.getData\("text\/html"\)/);
  assert.match(libraryEditorSrc, /clipboard\.getData\("text\/plain"\)/);
  assert.doesNotMatch(libraryEditorSrc, /handleVerdictSelectionCopy/);
});
