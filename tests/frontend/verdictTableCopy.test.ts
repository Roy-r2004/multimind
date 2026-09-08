import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { copyRichContent } from "../../src/lib/richClipboard.ts";
import {
  getVerdictBodyCopyHtml,
  markdownFromNodePosition,
  verdictTablePlainText,
} from "../../src/lib/verdictTableCopy.ts";

const root = join(dirname(fileURLToPath(import.meta.url)), "../..");
const chatSrc = readFileSync(join(root, "src/routes/chat.tsx"), "utf8");
const sharedSrc = readFileSync(join(root, "src/routes/shared.$token.tsx"), "utf8");
const messageContentSrc = readFileSync(
  join(root, "src/components/chat/MessageContent.tsx"),
  "utf8",
);

const TABLE_1 = `| Product | Price |
| --- | ---: |
| **Alpha** | [$10](https://ex.test) |`;

const TABLE_2 = `| Name | Qty |
| --- | --- |
| Beta | 3 |`;

const SOURCE = `Intro

${TABLE_1}

Between

${TABLE_2}
`;

const TABLE_1_HTML =
  `<table class="w-full min-w-[36rem]"><thead><tr><th>Product</th><th>Price</th></tr></thead>` +
  `<tbody><tr><td>Alpha</td><td>$10</td></tr></tbody></table>`;
const TABLE_2_HTML =
  `<table><thead><tr><th>Name</th><th>Qty</th></tr></thead>` +
  `<tbody><tr><td>Beta</td><td>3</td></tr></tbody></table>`;

function posFor(table: string) {
  const start = SOURCE.indexOf(table);
  return { start: { offset: start }, end: { offset: start + table.length } };
}

class MockClipboardItem {
  items: Record<string, Blob>;
  constructor(items: Record<string, Blob>) {
    this.items = items;
  }
}

type ClipboardStub = {
  writeCalls: MockClipboardItem[][];
  writeTextCalls: string[];
};

const originalClipboardItem = globalThis.ClipboardItem;
const originalNavigator = globalThis.navigator;

function installClipboard() {
  const stub: ClipboardStub = { writeCalls: [], writeTextCalls: [] };
  const clipboard = {
    write: async (items: MockClipboardItem[]) => {
      stub.writeCalls.push(items);
    },
    writeText: async (text: string) => {
      stub.writeTextCalls.push(text);
    },
  };
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    writable: true,
    value: { clipboard },
  });
  Object.defineProperty(globalThis, "ClipboardItem", {
    configurable: true,
    writable: true,
    value: MockClipboardItem,
  });
  return stub;
}

function restoreClipboard() {
  if (originalClipboardItem) {
    Object.defineProperty(globalThis, "ClipboardItem", {
      configurable: true,
      writable: true,
      value: originalClipboardItem,
    });
  } else {
    // @ts-expect-error test restore
    delete globalThis.ClipboardItem;
  }
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    writable: true,
    value: originalNavigator,
  });
}

test("Verdict Markdown tables get a per-instance Copy control; other variants do not", () => {
  assert.match(messageContentSrc, /function VerdictMarkdownTable/);
  assert.match(messageContentSrc, /createVerdictComponents/);
  assert.match(messageContentSrc, /data-verdict-table-copy-control/);
  assert.match(messageContentSrc, /table: VerdictMarkdownTable/);
  assert.match(messageContentSrc, /variant === "verdict"\) return createVerdictComponents/);
  assert.match(messageContentSrc, /if \(compact\) return compactComponents/);
  assert.doesNotMatch(
    messageContentSrc.slice(
      messageContentSrc.indexOf("function buildComponents"),
      messageContentSrc.indexOf("const compactComponents"),
    ),
    /data-verdict-table-copy-control/,
  );
  assert.doesNotMatch(sharedSrc, /variant="verdict"/);
  assert.match(chatSrc, /<MessageContent variant="verdict">\{turn\.verdict\.text\}/);
});

test("zero / one / two tables imply that many independent Copy controls", () => {
  const tableFn = messageContentSrc.slice(
    messageContentSrc.indexOf("function VerdictMarkdownTable"),
    messageContentSrc.indexOf("return {", messageContentSrc.indexOf("function VerdictMarkdownTable")),
  );
  assert.match(tableFn, /useRef<HTMLTableElement>/);
  assert.match(tableFn, /useState\(false\)/);
  assert.match(tableFn, /tableRef\.current\?\.outerHTML/);
  assert.doesNotMatch(messageContentSrc, /setTableCopied|copiedTables/);
});

test("first table copy uses only first table Markdown and HTML", async () => {
  const stub = installClipboard();
  try {
    const plainText = verdictTablePlainText(SOURCE, posFor(TABLE_1), TABLE_1_HTML);
    await copyRichContent({ plainText, html: TABLE_1_HTML });
    const item = stub.writeCalls[0]![0]!;
    const html = await item.items["text/html"]!.text();
    const text = await item.items["text/plain"]!.text();
    assert.equal(html, TABLE_1_HTML);
    assert.equal(html.includes("<table"), true);
    assert.equal((html.match(/<table\b/g) ?? []).length, 1);
    assert.doesNotMatch(html, /Copy table/);
    assert.doesNotMatch(html, /Name/);
    assert.doesNotMatch(html, /Beta/);
    assert.equal(text, TABLE_1);
    assert.match(text, /---:\s*\|/);
    assert.match(text, /\*\*Alpha\*\*/);
    assert.doesNotMatch(text, /Beta/);
    assert.doesNotMatch(text, /^Intro/m);
  } finally {
    restoreClipboard();
  }
});

test("second table copy uses only second table Markdown and HTML", async () => {
  const stub = installClipboard();
  try {
    const plainText = verdictTablePlainText(SOURCE, posFor(TABLE_2), TABLE_2_HTML);
    await copyRichContent({ plainText, html: TABLE_2_HTML });
    const item = stub.writeCalls[0]![0]!;
    assert.equal(await item.items["text/html"]!.text(), TABLE_2_HTML);
    assert.equal(await item.items["text/plain"]!.text(), TABLE_2);
    assert.doesNotMatch(await item.items["text/html"]!.text(), /Alpha/);
    assert.doesNotMatch(await item.items["text/plain"]!.text(), /Alpha/);
  } finally {
    restoreClipboard();
  }
});

test("HTML → GFM fallback is used when node offsets are missing", () => {
  const markdown = verdictTablePlainText(SOURCE, undefined, TABLE_2_HTML);
  assert.match(markdown, /\| Name \| Qty \|/);
  assert.match(markdown, /\| Beta \| 3 \|/);
  assert.doesNotMatch(markdown, /Alpha/);
});

test("exact source slice is preferred over HTML conversion", () => {
  const sliced = markdownFromNodePosition(SOURCE, posFor(TABLE_1));
  const withHtml = verdictTablePlainText(SOURCE, posFor(TABLE_1), TABLE_1_HTML);
  assert.equal(sliced, TABLE_1);
  assert.equal(withHtml, TABLE_1);
});

test("full Verdict Copy HTML is cloned and strips table-copy controls", () => {
  const liveInner =
    `<div class="message-content">` +
    `<div data-verdict-table-copy-control=""><button>Copy</button></div>` +
    `${TABLE_1_HTML}` +
    `<p>Body</p>` +
    `</div>`;
  const live = {
    innerHTML: liveInner,
    cloneNode(deep?: boolean) {
      assert.equal(deep, true);
      let cloneHtml = liveInner;
      return {
        get innerHTML() {
          return cloneHtml;
        },
        querySelectorAll(sel: string) {
          assert.equal(sel, "[data-verdict-table-copy-control]");
          return [
            {
              remove() {
                cloneHtml = cloneHtml.replace(
                  /<div data-verdict-table-copy-control=""><button>Copy<\/button><\/div>/,
                  "",
                );
              },
            },
          ];
        },
      };
    },
  };
  const html = getVerdictBodyCopyHtml(live as unknown as HTMLElement);
  assert.match(html ?? "", /<table\b/);
  assert.match(html ?? "", /Body/);
  assert.doesNotMatch(html ?? "", /data-verdict-table-copy-control/);
  assert.doesNotMatch(html ?? "", />Copy</);
  assert.match(live.innerHTML, /data-verdict-table-copy-control/);
});

test("full Verdict Copy still uses turn.verdict.text and cleaned body HTML", () => {
  assert.match(chatSrc, /<VerdictCopyButton\s+text=\{turn\.verdict\.text\}/);
  assert.match(chatSrc, /getHtml=\{\(\) => getVerdictBodyCopyHtml\(verdictContentRef\.current\)\}/);
  assert.match(chatSrc, /getVerdictBodyCopyHtml/);
  assert.doesNotMatch(
    chatSrc.slice(chatSrc.indexOf("<VerdictCopyButton"), chatSrc.indexOf("</VerdictCopyButton>")),
    /verdictContentRef\.current\?\.innerHTML/,
  );
});

test("table Copy feedback stays local to the table component", () => {
  const tableFn = messageContentSrc.slice(
    messageContentSrc.indexOf("function VerdictMarkdownTable"),
    messageContentSrc.indexOf("return {", messageContentSrc.indexOf("function VerdictMarkdownTable")),
  );
  assert.match(tableFn, /setCopied\(true\)/);
  assert.match(tableFn, /window\.setTimeout\(\(\) => setCopied\(false\), 2000\)/);
  assert.match(tableFn, /copied \? "Copied" : "Copy"/);
});
