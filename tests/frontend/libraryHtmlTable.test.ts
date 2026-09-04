import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import {
  applyLibraryTablePaste,
  decideLibraryTablePaste,
  htmlClipboardToMarkdown,
  htmlContainsUsableTable,
} from "../../src/lib/libraryHtmlTable.ts";
import { insertLibraryDocumentText } from "../../src/lib/libraryDocumentFormatting.ts";

const root = join(dirname(fileURLToPath(import.meta.url)), "../..");
const editorSrc = readFileSync(
  join(root, "src/components/library/LibraryDocumentEditor.tsx"),
  "utf8",
);

const BASIC_TABLE = `<table>
  <tr>
    <th>Product</th>
    <th>Price</th>
  </tr>
  <tr>
    <td>Laptop</td>
    <td>$1,000</td>
  </tr>
</table>`;

const THEAD_TABLE = `<table>
  <thead>
    <tr>
      <th>Product</th>
      <th>Price</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>Laptop</td>
      <td>$1,000</td>
    </tr>
    <tr>
      <td>Monitor</td>
      <td>$400</td>
    </tr>
  </tbody>
</table>`;

const TD_ONLY_TABLE = `<table>
  <tr><td>A</td><td>B</td></tr>
  <tr><td>1</td><td>2</td></tr>
</table>`;

const VERDICT_TABLE_HTML = `<div class="message-content"><div class="overflow-x-auto rounded-lg border border-border"><table class="w-full min-w-[36rem] table-auto border-collapse text-left"><thead class="bg-muted/50"><tr><th>Product</th><th>Price</th></tr></thead><tbody><tr><td>Laptop</td><td>$1,000</td></tr><tr><td>Monitor</td><td>$400</td></tr></tbody></table></div></div>`;

function gfm(header: string[], ...body: string[][]): string {
  const sep = header.map(() => "---");
  return [
    `| ${header.join(" | ")} |`,
    `| ${sep.join(" | ")} |`,
    ...body.map((row) => `| ${row.join(" | ")} |`),
  ].join("\n");
}

test("basic HTML table converts to GFM", () => {
  assert.equal(
    htmlClipboardToMarkdown(BASIC_TABLE),
    gfm(["Product", "Price"], ["Laptop", "$1,000"]),
  );
});

test("thead/th table converts to GFM", () => {
  assert.equal(
    htmlClipboardToMarkdown(THEAD_TABLE),
    gfm(["Product", "Price"], ["Laptop", "$1,000"], ["Monitor", "$400"]),
  );
});

test("td-only table uses the first row as the Markdown header", () => {
  assert.equal(htmlClipboardToMarkdown(TD_ONLY_TABLE), gfm(["A", "B"], ["1", "2"]));
});

test("literal pipes inside cells are escaped", () => {
  const html = `<table><tr><th>Name</th></tr><tr><td>A | B</td></tr></table>`;
  assert.equal(htmlClipboardToMarkdown(html), gfm(["Name"], ["A \\| B"]));
});

test("multiline and extra whitespace in cells is normalized", () => {
  const html = `<table><tr><th>Note</th></tr><tr><td>Hello
     world</td></tr></table>`;
  assert.equal(htmlClipboardToMarkdown(html), gfm(["Note"], ["Hello world"]));
});

test("uneven row lengths are padded with empty cells", () => {
  const html = `<table>
    <tr><th>A</th><th>B</th><th>C</th></tr>
    <tr><td>1</td><td>2</td></tr>
  </table>`;
  assert.equal(htmlClipboardToMarkdown(html), gfm(["A", "B", "C"], ["1", "2", ""]));
});

test("empty cells are preserved", () => {
  const html = `<table><tr><th></th><th>B</th></tr><tr><td>1</td><td></td></tr></table>`;
  assert.equal(htmlClipboardToMarkdown(html), gfm(["", "B"], ["1", ""]));
});

test("multiple HTML tables are converted and separated by a blank line", () => {
  const html = `${BASIC_TABLE}${TD_ONLY_TABLE}`;
  assert.equal(
    htmlClipboardToMarkdown(html),
    `${gfm(["Product", "Price"], ["Laptop", "$1,000"])}\n\n${gfm(["A", "B"], ["1", "2"])}`,
  );
});

test("malformed table HTML does not throw", () => {
  assert.doesNotThrow(() => htmlClipboardToMarkdown("<table><tr><td>Hi"));
  assert.doesNotThrow(() => htmlContainsUsableTable("<table"));
  assert.doesNotThrow(() => decideLibraryTablePaste("<table><tr>", "x"));
  const markdown = htmlClipboardToMarkdown("<table><tr><td>Hi");
  assert.match(markdown ?? "", /Hi/);
});

test("no HTML table leaves the native paste path untouched", () => {
  assert.equal(htmlContainsUsableTable("<p>Hello world</p>"), false);
  assert.equal(htmlClipboardToMarkdown("<p>Hello world</p>"), null);
  assert.deepEqual(decideLibraryTablePaste("<p>Hello world</p>", "Hello world"), {
    action: "native",
  });
  assert.equal(applyLibraryTablePaste("doc", 3, 3, "<p>Hello world</p>", "Hello world"), null);
  assert.equal(applyLibraryTablePaste("doc", 3, 3, "", "Hello world"), null);
});

test("rich table paste inserts at the current cursor", () => {
  const markdown = htmlClipboardToMarkdown(THEAD_TABLE)!;
  const edit = applyLibraryTablePaste("Hello world", 6, 6, THEAD_TABLE, "flattened");
  assert.ok(edit);
  assert.equal(edit.text, `Hello ${markdown}world`);
  assert.equal(edit.selectionStart, 6 + markdown.length);
  assert.equal(edit.selectionEnd, 6 + markdown.length);
});

test("rich table paste replaces selected text", () => {
  const markdown = htmlClipboardToMarkdown(BASIC_TABLE)!;
  const edit = applyLibraryTablePaste("Hello XX world", 6, 8, BASIC_TABLE, "plain");
  assert.ok(edit);
  assert.equal(edit.text, `Hello ${markdown} world`);
  assert.equal(edit.selectionStart, 6 + markdown.length);
  assert.equal(edit.selectionEnd, 6 + markdown.length);
});

test("surrounding existing document content is preserved", () => {
  const markdown = htmlClipboardToMarkdown(BASIC_TABLE)!;
  const edit = applyLibraryTablePaste("before\n\nafter", 8, 8, BASIC_TABLE, "plain");
  assert.ok(edit);
  assert.equal(edit.text, `before\n\n${markdown}after`);
  assert.match(edit.text, /^before\n\n/);
  assert.match(edit.text, /after$/);
});

test("cursor is positioned after inserted Markdown", () => {
  const markdown = htmlClipboardToMarkdown(BASIC_TABLE)!;
  const source = "ab";
  const edit = insertLibraryDocumentText(source, 1, 1, markdown);
  assert.equal(edit.selectionStart, 1 + markdown.length);
  assert.equal(edit.selectionEnd, 1 + markdown.length);
  assert.equal(edit.text.slice(edit.selectionEnd), "b");
});

test("Verdict-style rendered table HTML becomes GFM", () => {
  assert.equal(
    htmlClipboardToMarkdown(VERDICT_TABLE_HTML),
    gfm(["Product", "Price"], ["Laptop", "$1,000"], ["Monitor", "$400"]),
  );
});

test("surrounding HTML text is not duplicated when converting tables", () => {
  const html = `<p>Intro</p>${BASIC_TABLE}<p>Outro</p>`;
  const markdown = htmlClipboardToMarkdown(html)!;
  assert.equal(markdown.indexOf("Intro"), markdown.lastIndexOf("Intro"));
  assert.equal(markdown.indexOf("Outro"), markdown.lastIndexOf("Outro"));
  assert.match(markdown, /^Intro\n\n\| Product \| Price \|/);
  assert.match(markdown, /\$1,000 \|\n\nOutro$/);
});

test("flattened plain text is not used when HTML tables convert", () => {
  const decision = decideLibraryTablePaste(THEAD_TABLE, "Product\tPrice\nLaptop\t$1,000");
  assert.equal(decision.action, "insert");
  assert.equal(decision.markdown, gfm(["Product", "Price"], ["Laptop", "$1,000"], ["Monitor", "$400"]));
});

test("unusable table markup does not intercept native paste", () => {
  assert.deepEqual(decideLibraryTablePaste("<table></table>", "plain fallback"), {
    action: "native",
  });
  assert.deepEqual(decideLibraryTablePaste("<table><tbody></tbody></table>", "plain fallback"), {
    action: "native",
  });
});

test("editor paste uses the existing applyEdit / onChange path", () => {
  assert.match(editorSrc, /onPaste=\{onPaste\}/);
  assert.match(editorSrc, /applyLibraryTablePaste\(/);
  assert.match(editorSrc, /clipboard\.getData\("text\/html"\)/);
  assert.match(editorSrc, /clipboard\.getData\("text\/plain"\)/);
  assert.match(editorSrc, /if \(!edit\) return;/);
  assert.match(editorSrc, /event\.preventDefault\(\);\s*applyEdit\(edit\);/s);
  assert.match(editorSrc, /function applyEdit\(edit: TextareaEdit\) \{/);
  assert.match(editorSrc, /onChange\(edit\.text\)/);
});

test("paste helper falls back to text/plain after a conversion miss", () => {
  const helperSrc = readFileSync(join(root, "src/lib/libraryHtmlTable.ts"), "utf8");
  assert.match(helperSrc, /return \{ action: "insert", markdown: plainText \}/);
  assert.match(helperSrc, /if \(!htmlContainsUsableTable\(html\)\) return \{ action: "native" \}/);
});
