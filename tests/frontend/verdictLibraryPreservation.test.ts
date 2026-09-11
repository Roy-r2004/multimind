import assert from "node:assert/strict";
import test from "node:test";
import { unified } from "unified";
import remarkParse from "remark-parse";
import remarkRehype from "remark-rehype";
import { copyRichContent } from "../../src/lib/richClipboard.ts";
import { applyLibraryTablePaste, htmlClipboardToMarkdown } from "../../src/lib/libraryHtmlTable.ts";
import { libraryDocumentPreviewContent } from "../../src/lib/libraryDocumentPreview.ts";
import { preserveOrderedListValues } from "../../src/lib/preserveOrderedListValues.ts";

const cases = [
  "1. Alpha\n2. Beta\n3. Gamma",
  "3. Alpha\n4. Beta\n5. Gamma",
  "1. Alpha\n   1. Nested A\n   2. Nested B\n2. Beta",
  "5. Alpha\n   3. Nested A\n   4. Nested B\n6. Beta",
  "5. Alpha\n3. Nested A\n4. Nested B\n6. Beta",
  "- Bullet\n  3. Third\n  8. Eighth\n- Last",
  "3. **Bold** and *italic* [link](https://example.com)\n7. Final",
  '| Numbers |\n| --- |\n| 1. |\n| 02 |\n| 7.5 |',
  '# Rehabilitation review\n\nRP-REHAB: \\~20 core archetypes\n\n3. Preserve **exact words**.\n8. Compare *carefully*.\n\n> Important note\n\n## Evidence\n\n| Number | Finding |\n| --- | --- |\n| 02 | RP-REHAB: ~20 core archetypes |\n\n`1.` remains code.\n\n```text\n3. Not a list\n```\n\n---\n',
];

test("Verdict rich clipboard → Library draft content_text preserves every source character", async () => {
  const navigatorDescriptor = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  const itemDescriptor = Object.getOwnPropertyDescriptor(globalThis, "ClipboardItem");
  let payload: Record<string, Blob> = {};
  class Item {
    items: Record<string, Blob>;
    constructor(items: Record<string, Blob>) { this.items = items; }
  }
  Object.defineProperty(globalThis, "ClipboardItem", { configurable: true, value: Item });
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: {
    clipboard: { write: async (items: Item[]) => { payload = items[0].items; } },
  } });
  try {
    for (const source of cases) {
      // Deliberately lossy HTML must never replace the source Markdown.
      await copyRichContent({ plainText: source, html: '<ol><li>Changed</li></ol><table><tr><td>02</td></tr></table>', plainTextIsMarkdown: true });
      const plain = await payload["text/plain"].text();
      const html = await payload["text/html"].text();
      assert.equal(plain, source);
      const edit = applyLibraryTablePaste("", 0, 0, html, plain);
      assert.ok(edit);
      const content_text = edit.text;
      assert.equal(content_text, source);
      assert.equal(libraryDocumentPreviewContent(content_text), source);
      assert.equal(applyLibraryTablePaste("before XX after", 7, 9, html, plain)?.text, `before ${source} after`);
    }
  } finally {
    if (navigatorDescriptor) Object.defineProperty(globalThis, "navigator", navigatorDescriptor);
    else Reflect.deleteProperty(globalThis, "navigator");
    if (itemDescriptor) Object.defineProperty(globalThis, "ClipboardItem", itemDescriptor);
    else Reflect.deleteProperty(globalThis, "ClipboardItem");
  }
});

test("external HTML list conversion preserves start, item values, nesting and inline content", () => {
  assert.equal(applyLibraryTablePaste("", 0, 0, '<ol start="3"><li>First</li><li value="8">Second</li></ol>', "First\nSecond")?.text, "3. First\n8. Second");
  assert.equal(htmlClipboardToMarkdown('<ol start="3"><li>First</li><li>Second</li></ol>'), "3. First\n4. Second");
  assert.equal(htmlClipboardToMarkdown('<div><ol start=5><li>Alpha<ol start="3"><li>Nested A</li><li value="8">Nested B</li></ol></li><li value=6>Beta</li></ol></div>'), "5. Alpha\n\n   3. Nested A\n   8. Nested B\n6. Beta");
  assert.equal(htmlClipboardToMarkdown('<ol start="3"><li><strong>Bold</strong> <em>italic</em> <a href="https://example.com">link</a></li><li value="9">Last</li></ol>'), '3. **Bold** *italic* [link](<https://example.com>)\n9. Last');
  const table = '<table><tr><th>Numbers</th></tr><tr><td>1.</td></tr><tr><td>02</td></tr><tr><td>7.5</td></tr></table>';
  assert.equal(htmlClipboardToMarkdown('<ol start="3"><li>First</li><li value="7">Last</li></ol>' + table), '3. First\n7. Last\n\n' + cases[7]);
});

test("shared Markdown pipeline retains every explicit ordered item value", async () => {
  const source = '5. Alpha\n\n   3. Nested A\n   8. Nested B\n2. Beta\n\n- Bullet\n\n```text\n9. Code\n```';
  const processor = unified().use(remarkParse).use(remarkRehype).use(preserveOrderedListValues);
  const tree = await processor.run(processor.parse(source), { value: source });
  const values: unknown[] = [];
  function walk(node: any) {
    if (node.tagName === "li") values.push(node.properties.value);
    node.children?.forEach(walk);
  }
  walk(tree);
  assert.deepEqual(values, [5, 3, 8, 2, undefined]);
});
