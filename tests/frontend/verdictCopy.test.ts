import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { copyRichContent } from "../../src/lib/richClipboard.ts";

const root = join(dirname(fileURLToPath(import.meta.url)), "../..");
const chatSrc = readFileSync(join(root, "src/routes/chat.tsx"), "utf8");
const copyButtonSrc = readFileSync(
  join(root, "src/components/chat/VerdictCopyButton.tsx"),
  "utf8",
);
const messageContentSrc = readFileSync(
  join(root, "src/components/chat/MessageContent.tsx"),
  "utf8",
);

const MARKDOWN = `| Product | Price | Status |
| --- | --- | --- |
| Alpha | $10 | Ready |`;

const RENDERED_TABLE_HTML = `<div class="message-content"><table class="w-full min-w-[36rem] table-auto border-collapse text-left"><thead><tr><th>Product</th><th>Price</th><th>Status</th></tr></thead><tbody><tr><td>Alpha</td><td>$10</td><td>Ready</td></tr></tbody></table></div>`;

const CARD_HTML = `<h3>Verdict</h3><span>Judge: GPT</span><span>Best: Claude</span><span>$0.12</span><button>Copy</button><button>Pin</button><button>Save</button><button>Challenge</button>${RENDERED_TABLE_HTML}<div class="reason">Secret judge rationale</div>`;

class MockClipboardItem {
  items: Record<string, Blob>;
  constructor(items: Record<string, Blob>) {
    this.items = items;
  }
}

type ClipboardStub = {
  writeCalls: MockClipboardItem[][];
  writeTextCalls: string[];
  writeImpl?: (items: MockClipboardItem[]) => Promise<void>;
};

const originalClipboardItem = globalThis.ClipboardItem;
const originalNavigator = globalThis.navigator;

function installClipboard(options?: { writeThrows?: boolean; omitClipboardItem?: boolean }) {
  const stub: ClipboardStub = { writeCalls: [], writeTextCalls: [] };
  stub.writeImpl = async (items) => {
    if (options?.writeThrows) throw new Error("rich clipboard denied");
    stub.writeCalls.push(items);
  };

  const clipboard = {
    write: async (items: MockClipboardItem[]) => stub.writeImpl!(items),
    writeText: async (text: string) => {
      stub.writeTextCalls.push(text);
    },
  };

  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    writable: true,
    value: { clipboard },
  });

  if (options?.omitClipboardItem) {
    // @ts-expect-error test: rich clipboard constructor missing
    delete globalThis.ClipboardItem;
  } else {
    Object.defineProperty(globalThis, "ClipboardItem", {
      configurable: true,
      writable: true,
      value: MockClipboardItem,
    });
  }

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

test("rich clipboard write includes text/plain Markdown and text/html", async () => {
  const stub = installClipboard();
  try {
    await copyRichContent({ plainText: MARKDOWN, html: RENDERED_TABLE_HTML });
    assert.equal(stub.writeCalls.length, 1);
    assert.equal(stub.writeTextCalls.length, 0);
    const item = stub.writeCalls[0]![0]!;
    assert.ok(item.items["text/plain"]);
    assert.ok(item.items["text/html"]);
    assert.equal(await item.items["text/plain"]!.text(), MARKDOWN);
    const html = await item.items["text/html"]!.text();
    assert.equal(html, RENDERED_TABLE_HTML);
    assert.match(html, /<table\b/);
    assert.match(html, /<thead\b/);
    assert.match(html, /<th>\s*Product/);
    assert.match(html, /<td>\s*Alpha/);
  } finally {
    restoreClipboard();
  }
});

test("plain text value is the original Verdict Markdown", async () => {
  const stub = installClipboard();
  try {
    await copyRichContent({ plainText: MARKDOWN, html: RENDERED_TABLE_HTML });
    assert.equal(await stub.writeCalls[0]![0]!.items["text/plain"]!.text(), MARKDOWN);
  } finally {
    restoreClipboard();
  }
});

test("copied HTML is the rendered content ref, not the card or verdict reason", async () => {
  const stub = installClipboard();
  const contentEl = { innerHTML: RENDERED_TABLE_HTML };
  const cardEl = { innerHTML: CARD_HTML };
  try {
    await copyRichContent({
      plainText: MARKDOWN,
      html: contentEl.innerHTML,
    });
    const html = await stub.writeCalls[0]![0]!.items["text/html"]!.text();
    assert.equal(html, contentEl.innerHTML);
    assert.notEqual(html, cardEl.innerHTML);
    assert.doesNotMatch(html, /Secret judge rationale/);
    assert.doesNotMatch(html, /Judge:/);
    assert.doesNotMatch(html, /Best:/);
    assert.doesNotMatch(html, />Copy</);
    assert.doesNotMatch(html, />Pin</);
    assert.doesNotMatch(html, />Save</);
    assert.doesNotMatch(html, />Challenge</);
    assert.doesNotMatch(stub.writeCalls[0]![0]!.items["text/plain"] ? await stub.writeCalls[0]![0]!.items["text/plain"]!.text() : "", /Secret judge rationale/);
  } finally {
    restoreClipboard();
  }
});

test("rich clipboard write failure falls back to writeText with original Markdown", async () => {
  const stub = installClipboard({ writeThrows: true });
  try {
    await copyRichContent({ plainText: MARKDOWN, html: RENDERED_TABLE_HTML });
    assert.equal(stub.writeCalls.length, 0);
    assert.deepEqual(stub.writeTextCalls, [MARKDOWN]);
  } finally {
    restoreClipboard();
  }
});

test("missing ClipboardItem falls back to writeText", async () => {
  const stub = installClipboard({ omitClipboardItem: true });
  try {
    await copyRichContent({ plainText: MARKDOWN, html: RENDERED_TABLE_HTML });
    assert.equal(stub.writeCalls.length, 0);
    assert.deepEqual(stub.writeTextCalls, [MARKDOWN]);
  } finally {
    restoreClipboard();
  }
});

test("Verdict Copy captures only primary MessageContent innerHTML", () => {
  assert.match(chatSrc, /const verdictContentRef = useRef<HTMLDivElement>\(null\)/);
  assert.match(
    chatSrc,
    /<VerdictCopyButton\s+text=\{turn\.verdict\.text\}\s+getHtml=\{\(\) => verdictContentRef\.current\?\.innerHTML\}/,
  );
  assert.match(
    chatSrc,
    /<div ref=\{verdictContentRef\}>\s*<MessageContent variant="verdict">\{turn\.verdict\.text\}<\/MessageContent>\s*<\/div>/,
  );
  assert.match(chatSrc, /\{turn\.verdict\.reason && \(/);
  const contentRefBlock = chatSrc.slice(
    chatSrc.indexOf("<div ref={verdictContentRef}>"),
    chatSrc.indexOf("</div>", chatSrc.indexOf("<div ref={verdictContentRef}>")) + 6,
  );
  assert.doesNotMatch(contentRefBlock, /turn\.verdict\.reason/);
  assert.doesNotMatch(contentRefBlock, /Challenge/);
  assert.doesNotMatch(contentRefBlock, /Judge:/);
  assert.match(messageContentSrc, /table: \(\{ children \}\) =>/);
});

test("Copy success feedback still runs after rich clipboard copy", () => {
  assert.match(copyButtonSrc, /await copyRichContent\(\{ plainText: text, html: getHtml\?\.\(\) \}\)/);
  assert.match(copyButtonSrc, /setCopied\(true\)/);
  assert.match(copyButtonSrc, /toast\.success\(successMessage\)/);
  assert.match(copyButtonSrc, /window\.setTimeout\(\(\) => setCopied\(false\), 2000\)/);
  assert.match(copyButtonSrc, /toast\.error\(errorMessage\)/);
});
