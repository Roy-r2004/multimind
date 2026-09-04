/**
 * Convert clipboard HTML tables to GFM Markdown for the Library textarea.
 *
 * Known V1 limitations:
 * - colspan/rowspan are flattened (each th/td is one cell; no merged-cell layout)
 * - nested tables become readable cell text, not nested Markdown tables
 * - source styling (colors, widths, Word mso-*, Google Docs CSS) is discarded
 * - non-table HTML is reduced to normalized text, not a full HTML→Markdown conversion
 */

import { insertLibraryDocumentText, type TextareaEdit } from "./libraryDocumentFormatting.ts";

export type LibraryTablePasteDecision =
  | { action: "native" }
  | { action: "insert"; markdown: string };

type MiniEl = { tag: string; children: MiniNode[] };
type MiniText = { text: string };
type MiniNode = MiniEl | MiniText;

const VOID_TAGS = new Set([
  "area",
  "base",
  "br",
  "col",
  "embed",
  "hr",
  "img",
  "input",
  "link",
  "meta",
  "param",
  "source",
  "track",
  "wbr",
]);

function isEl(node: MiniNode): node is MiniEl {
  return "tag" in node;
}

/** True when clipboard HTML has at least one table row with a cell. */
export function htmlContainsUsableTable(html: string): boolean {
  if (!html || !/<table[\s>]/i.test(html)) return false;
  try {
    return usableTables(parseHtmlTree(html)).length > 0;
  } catch {
    return false;
  }
}

/**
 * Convert clipboard HTML to Markdown, preserving every top-level table as GFM.
 * Returns null when there is no usable table (caller must not intercept paste).
 */
export function htmlClipboardToMarkdown(html: string): string | null {
  if (!htmlContainsUsableTable(html)) return null;
  const tree = parseHtmlTree(html);
  const parts = blocksFromNode(tree).map((part) => part.trim()).filter(Boolean);
  const markdown = parts.join("\n\n").trim();
  return markdown.length > 0 ? markdown : null;
}

/**
 * Native paste when there is no usable HTML table.
 * Otherwise insert converted GFM, or plain text if conversion fails.
 */
export function decideLibraryTablePaste(html: string, plainText: string): LibraryTablePasteDecision {
  if (!htmlContainsUsableTable(html)) return { action: "native" };
  try {
    const markdown = htmlClipboardToMarkdown(html);
    if (markdown) return { action: "insert", markdown };
  } catch {
    // Fall through to plain text so Copy/paste stays usable.
  }
  return { action: "insert", markdown: plainText };
}

export function applyLibraryTablePaste(
  text: string,
  selectionStart: number,
  selectionEnd: number,
  html: string,
  plainText: string,
): TextareaEdit | null {
  const decision = decideLibraryTablePaste(html, plainText);
  if (decision.action === "native") return null;
  return insertLibraryDocumentText(text, selectionStart, selectionEnd, decision.markdown);
}

function usableTables(root: MiniEl): MiniEl[] {
  return collectTables(root).filter((table) => tableToRows(table).some((row) => row.length > 0));
}

function collectTables(node: MiniEl, into: MiniEl[] = []): MiniEl[] {
  if (node.tag === "table") {
    into.push(node);
    return into;
  }
  for (const child of node.children) {
    if (isEl(child)) collectTables(child, into);
  }
  return into;
}

function blocksFromNode(node: MiniEl): string[] {
  if (node.tag === "table") {
    const markdown = tableToGfm(node);
    return markdown ? [markdown] : [];
  }
  if (!containsTable(node)) {
    const text = normalizeCellText(textFromNode(node));
    return text ? [text] : [];
  }

  const parts: string[] = [];
  let textBuf = "";
  const flushText = () => {
    const text = normalizeCellText(textBuf);
    if (text) parts.push(text);
    textBuf = "";
  };

  for (const child of node.children) {
    if (isEl(child) && (child.tag === "table" || containsTable(child))) {
      flushText();
      parts.push(...blocksFromNode(child));
      continue;
    }
    if (isEl(child) && isBlockTag(child.tag)) {
      flushText();
      const text = normalizeCellText(textFromNode(child));
      if (text) parts.push(text);
      continue;
    }
    textBuf += isEl(child) ? textFromNode(child) : child.text;
  }
  flushText();
  return parts;
}

function containsTable(node: MiniEl): boolean {
  if (node.tag === "table") return true;
  return node.children.some((child) => isEl(child) && containsTable(child));
}

function isBlockTag(tag: string): boolean {
  return (
    tag === "p" ||
    tag === "div" ||
    tag === "br" ||
    tag === "li" ||
    tag === "ul" ||
    tag === "ol" ||
    tag === "blockquote" ||
    tag === "pre" ||
    tag === "hr" ||
    /^h[1-6]$/.test(tag)
  );
}

function tableToGfm(table: MiniEl): string | null {
  const rows = tableToRows(table);
  if (rows.length === 0) return null;
  const width = Math.max(0, ...rows.map((row) => row.length));
  if (width === 0) return null;
  const normalized = rows.map((row) => {
    const cells = row.map(escapePipes);
    while (cells.length < width) cells.push("");
    return cells;
  });
  const header = normalized[0]!;
  const body = normalized.slice(1);
  const lines = [
    gfmRow(header),
    gfmRow(header.map(() => "---")),
    ...body.map(gfmRow),
  ];
  return lines.join("\n");
}

function tableToRows(table: MiniEl): string[][] {
  const rows: string[][] = [];
  for (const child of table.children) {
    if (!isEl(child)) continue;
    if (child.tag === "tr") {
      rows.push(rowCells(child));
      continue;
    }
    if (child.tag === "thead" || child.tag === "tbody" || child.tag === "tfoot") {
      for (const row of child.children) {
        if (isEl(row) && row.tag === "tr") rows.push(rowCells(row));
      }
    }
  }
  return rows;
}

function rowCells(row: MiniEl): string[] {
  const cells: string[] = [];
  for (const child of row.children) {
    if (isEl(child) && (child.tag === "th" || child.tag === "td")) {
      cells.push(normalizeCellText(textFromNode(child)));
    }
  }
  return cells;
}

function gfmRow(cells: string[]): string {
  return `| ${cells.join(" | ")} |`;
}

function escapePipes(value: string): string {
  return value.replace(/\|/g, "\\|");
}

function normalizeCellText(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

function textFromNode(node: MiniNode): string {
  if (!isEl(node)) return node.text;
  if (node.tag === "br") return " ";
  if (node.tag === "table") {
    return tableToRows(node)
      .map((row) => row.join(" "))
      .join("; ");
  }
  return node.children.map(textFromNode).join(node.tag === "p" || node.tag === "div" ? " " : "");
}

function parseHtmlTree(html: string): MiniEl {
  try {
    if (typeof globalThis.DOMParser === "function") {
      const doc = new DOMParser().parseFromString(html, "text/html");
      if (doc.body) return fromDom(doc.body);
    }
  } catch {
    // Use the dependency-free parser below.
  }
  return parseMiniHtml(html);
}

function fromDom(element: Element): MiniEl {
  const children: MiniNode[] = [];
  for (const child of Array.from(element.childNodes)) {
    if (child.nodeType === 3) {
      children.push({ text: child.textContent ?? "" });
    } else if (child.nodeType === 1) {
      children.push(fromDom(child as Element));
    }
  }
  return { tag: element.tagName.toLowerCase(), children };
}

function parseMiniHtml(html: string): MiniEl {
  const root: MiniEl = { tag: "root", children: [] };
  const stack: MiniEl[] = [root];
  const input = html.replace(/<!--[\s\S]*?-->/g, "");
  let i = 0;

  while (i < input.length) {
    if (input[i] !== "<") {
      const next = input.indexOf("<", i);
      const raw = next === -1 ? input.slice(i) : input.slice(i, next);
      stack[stack.length - 1]!.children.push({ text: decodeEntities(raw) });
      i = next === -1 ? input.length : next;
      continue;
    }
    if (input.startsWith("</", i)) {
      const end = input.indexOf(">", i + 2);
      const name = input
        .slice(i + 2, end === -1 ? input.length : end)
        .trim()
        .split(/[\s/]/)[0]!
        .toLowerCase();
      i = end === -1 ? input.length : end + 1;
      for (let depth = stack.length - 1; depth > 0; depth -= 1) {
        if (stack[depth]!.tag === name) {
          stack.length = depth;
          break;
        }
      }
      continue;
    }
    if (input.startsWith("<!", i) || input.startsWith("<?", i)) {
      const end = input.indexOf(">", i + 2);
      i = end === -1 ? input.length : end + 1;
      continue;
    }

    const tagMatch = input.slice(i).match(/^<([a-zA-Z][\w:.-]*)/);
    if (!tagMatch) {
      stack[stack.length - 1]!.children.push({ text: "<" });
      i += 1;
      continue;
    }
    const tag = tagMatch[1]!.toLowerCase();
    const afterName = i + 1 + tagMatch[1]!.length;
    const tagEnd = findTagEnd(input, afterName);
    const rawTag = input.slice(i, tagEnd === -1 ? input.length : tagEnd + 1);
    const selfClosing = VOID_TAGS.has(tag) || /\/\s*>$/.test(rawTag);
    i = tagEnd === -1 ? input.length : tagEnd + 1;
    if (tag === "script" || tag === "style") {
      const close = input.toLowerCase().indexOf(`</${tag}`, i);
      i = close === -1 ? input.length : input.indexOf(">", close) + 1;
      continue;
    }
    const el: MiniEl = { tag, children: [] };
    stack[stack.length - 1]!.children.push(el);
    if (!selfClosing) stack.push(el);
  }

  return root;
}

function findTagEnd(input: string, from: number): number {
  let quote: string | null = null;
  for (let i = from; i < input.length; i += 1) {
    const ch = input[i]!;
    if (quote) {
      if (ch === quote) quote = null;
      continue;
    }
    if (ch === '"' || ch === "'") {
      quote = ch;
      continue;
    }
    if (ch === ">") return i;
  }
  return -1;
}

function decodeEntities(value: string): string {
  return value
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&apos;|&#39;/g, "'")
    .replace(/&#x([0-9a-f]+);/gi, (_, hex: string) => String.fromCharCode(parseInt(hex, 16)))
    .replace(/&#(\d+);/g, (_, dec: string) => String.fromCharCode(Number(dec)));
}
