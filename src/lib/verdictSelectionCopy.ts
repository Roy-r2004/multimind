import { VERDICT_TABLE_COPY_CONTROL } from "./verdictTableCopy.ts";

/** Marker on the rendered Verdict body so selection-copy stays scoped. */
export const VERDICT_COPY_ROOT_SELECTOR = "[data-verdict-copy-root]";

export type CopyAncestor = {
  tag: string;
  href?: string;
};

export type SelectionCopyPayload = {
  html: string;
  plainText: string;
};

const SEMANTIC_WRAP_TAGS = new Set([
  "ul",
  "ol",
  "li",
  "p",
  "blockquote",
  "pre",
  "code",
  "strong",
  "b",
  "em",
  "i",
  "a",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "table",
  "thead",
  "tbody",
  "tfoot",
  "tr",
  "th",
  "td",
]);

const FORM_FIELD_SELECTOR = "textarea, input, select, [contenteditable='true']";

const VOID_TAGS = new Set(["area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"]);

type MiniEl = { kind: "el"; tag: string; href?: string; children: MiniNode[] };
type MiniText = { kind: "text"; value: string };
type MiniNode = MiniEl | MiniText;

function isEl(node: MiniNode): node is MiniEl {
  return node.kind === "el";
}

/**
 * Keep list/table/inline ancestors that cloneContents omits (the common
 * ancestor and everything above it). Stop before wrapping an outer <li>
 * around a nested list that is already the copy root — that parent item's
 * own text was not selected.
 */
export function filterWrapAncestors(ancestors: CopyAncestor[]): CopyAncestor[] {
  const result: CopyAncestor[] = [];
  let wrappedList = false;
  for (const ancestor of ancestors) {
    const tag = ancestor.tag.toLowerCase();
    if (!SEMANTIC_WRAP_TAGS.has(tag)) continue;
    if (tag === "ul" || tag === "ol") {
      result.push({ ...ancestor, tag });
      wrappedList = true;
      continue;
    }
    if (tag === "li") {
      if (wrappedList) break;
      result.push({ ...ancestor, tag });
      continue;
    }
    if (wrappedList) continue;
    result.push({ ...ancestor, tag, href: tag === "a" ? ancestor.href : undefined });
  }
  return result;
}

export function wrapInnerHtml(innerHtml: string, ancestors: CopyAncestor[]): string {
  let html = innerHtml;
  for (const ancestor of filterWrapAncestors(ancestors)) {
    if (ancestor.tag === "a" && ancestor.href) {
      html = `<a href="${escapeAttr(ancestor.href)}">${html}</a>`;
    } else {
      html = `<${ancestor.tag}>${html}</${ancestor.tag}>`;
    }
  }
  return html;
}

export function stripVerdictCopyControls(html: string): string {
  return html.replace(/<[^>]*data-verdict-table-copy-control[^>]*>[\s\S]*?<\/div>/gi, "");
}

export function htmlToCopyPlainText(html: string): string {
  const roots = parseMiniHtml(html);
  const text = roots.map((node) => renderPlain(node, { depth: 0, ordered: false, index: 0 })).join("");
  return text.replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
}

export function buildSelectionCopyPayload(
  innerHtml: string,
  ancestors: CopyAncestor[],
): SelectionCopyPayload | null {
  const wrapped = wrapInnerHtml(stripVerdictCopyControls(innerHtml), ancestors);
  const plainText = htmlToCopyPlainText(wrapped);
  if (!plainText) return null;
  return { html: wrapped, plainText };
}

export function isFormFieldCopyTarget(target: EventTarget | Node | null | undefined): boolean {
  const el = nodeElement(target);
  if (!el) return false;
  if (typeof el.closest === "function" && el.closest(FORM_FIELD_SELECTOR)) return true;
  const tag = el.tagName?.toLowerCase();
  return tag === "textarea" || tag === "input" || tag === "select";
}

export function verdictCopyRootFromNode(node: Node | null | undefined): Element | null {
  const el = nodeElement(node);
  if (!el || typeof el.closest !== "function") return null;
  return el.closest(VERDICT_COPY_ROOT_SELECTOR);
}

/**
 * Intercept Ctrl/Cmd+C for a non-collapsed selection inside a Verdict body.
 * Writes text/html + text/plain and preventDefault only when that succeeds.
 */
export function handleVerdictSelectionCopy(event: ClipboardEvent): boolean {
  if (event.defaultPrevented) return false;
  const data = event.clipboardData;
  if (!data) return false;
  if (isFormFieldCopyTarget(event.target)) return false;

  const selection = typeof window !== "undefined" ? window.getSelection() : null;
  if (!selection || selection.isCollapsed || selection.rangeCount === 0) return false;

  let range: Range;
  try {
    range = selection.getRangeAt(0);
  } catch {
    return false;
  }

  if (isFormFieldCopyTarget(range.startContainer) || isFormFieldCopyTarget(range.endContainer)) {
    return false;
  }

  const startRoot = verdictCopyRootFromNode(range.startContainer);
  const endRoot = verdictCopyRootFromNode(range.endContainer);
  if (!startRoot || startRoot !== endRoot) return false;

  const payload = selectionRangeToClipboard(range, startRoot);
  if (!payload) return false;

  try {
    data.setData("text/html", payload.html);
    data.setData("text/plain", payload.plainText);
    event.preventDefault();
    return true;
  } catch {
    return false;
  }
}

export function selectionRangeToClipboard(range: Range, root: Element): SelectionCopyPayload | null {
  if (typeof document === "undefined") return null;
  try {
    const fragment = range.cloneContents();
    const holder = document.createElement("div");
    holder.appendChild(fragment);
    holder.querySelectorAll(VERDICT_TABLE_COPY_CONTROL).forEach((node) => node.remove());
    const ancestors = collectCopyAncestors(range, root);
    return buildSelectionCopyPayload(holder.innerHTML, ancestors);
  } catch {
    return null;
  }
}

export function collectCopyAncestors(range: Range, root: Element): CopyAncestor[] {
  let node: Node | null = range.commonAncestorContainer;
  if (node.nodeType !== 1) node = node.parentElement;
  const ancestors: CopyAncestor[] = [];
  while (node && node instanceof Element && node !== root && root.contains(node)) {
    const tag = node.tagName.toLowerCase();
    const spec: CopyAncestor = { tag };
    if (tag === "a") {
      const href = node.getAttribute("href");
      if (href) spec.href = href;
    }
    ancestors.push(spec);
    node = node.parentElement;
  }
  return ancestors;
}

function nodeElement(node: EventTarget | Node | null | undefined): Element | null {
  if (!node) return null;
  if (typeof Element !== "undefined" && node instanceof Element) return node;
  if (typeof Node !== "undefined" && node instanceof Node) {
    return node.nodeType === 1 ? (node as Element) : node.parentElement;
  }
  const maybe = node as { tagName?: string; closest?: (s: string) => Element | null };
  if (typeof maybe.closest === "function" || maybe.tagName) return maybe as Element;
  return null;
}

function escapeAttr(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
}

function renderPlain(
  node: MiniNode,
  ctx: { depth: number; ordered: boolean; index: number },
): string {
  if (!isEl(node)) return collapseInline(node.value);

  const tag = node.tag;
  if (tag === "br") return "\n";
  if (tag === "hr") return "\n---\n\n";
  if (tag === "script" || tag === "style") return "";

  if (tag === "ul") return renderList(node, false, ctx.depth);
  if (tag === "ol") return renderList(node, true, ctx.depth);

  if (tag === "li") {
    const indent = "    ".repeat(ctx.depth);
    const marker = ctx.ordered ? `${ctx.index}. ` : "• ";
    const inline: MiniNode[] = [];
    const nested: MiniEl[] = [];
    for (const child of node.children) {
      if (isEl(child) && (child.tag === "ul" || child.tag === "ol")) nested.push(child);
      else inline.push(child);
    }
    const head = collapseInline(inline.map((child) => renderInline(child)).join("")).trim();
    const nestedText = nested.map((child) => renderPlain(child, { ...ctx, depth: ctx.depth + 1 })).join("");
    if (head && nestedText) return `${indent}${marker}${head}\n${nestedText}`;
    if (head) return `${indent}${marker}${head}\n`;
    return nestedText;
  }

  if (tag === "table") return `${tablePlain(node)}\n\n`;
  if (tag === "pre") return `${rawText(node).replace(/\s+$/, "")}\n\n`;
  if (tag === "blockquote") {
    const inner = node.children.map((child) => renderPlain(child, ctx)).join("").trim();
    return `${inner
      .split("\n")
      .map((line) => (line ? `> ${line}` : ">"))
      .join("\n")}\n\n`;
  }
  if (/^h[1-6]$/.test(tag) || tag === "p") {
    const inner = collapseInline(node.children.map((child) => renderInline(child)).join("")).trim();
    return inner ? `${inner}\n\n` : "";
  }
  if (tag === "tr") return `${node.children.filter(isCell).map((cell) => collapseInline(renderInline(cell)).trim()).join(" | ")}\n`;

  return node.children.map((child) => renderPlain(child, ctx)).join("");
}

function renderList(node: MiniEl, ordered: boolean, depth: number): string {
  let index = 0;
  const items = node.children
    .filter((child): child is MiniEl => isEl(child) && child.tag === "li")
    .map((li) => {
      index += 1;
      return renderPlain(li, { depth, ordered, index });
    });
  const text = items.join("");
  return depth === 0 ? `${text}\n` : text;
}

function isCell(node: MiniNode): node is MiniEl {
  return isEl(node) && (node.tag === "th" || node.tag === "td");
}

function tablePlain(table: MiniEl): string {
  const rows: MiniEl[] = [];
  for (const child of table.children) {
    if (!isEl(child)) continue;
    if (child.tag === "tr") rows.push(child);
    if (child.tag === "thead" || child.tag === "tbody" || child.tag === "tfoot") {
      for (const row of child.children) {
        if (isEl(row) && row.tag === "tr") rows.push(row);
      }
    }
  }
  return rows
    .map((row) =>
      row.children
        .filter(isCell)
        .map((cell) => collapseInline(renderInline(cell)).trim())
        .join(" | "),
    )
    .filter(Boolean)
    .join("\n");
}

function renderInline(node: MiniNode): string {
  if (!isEl(node)) return node.value;
  if (node.tag === "br") return "\n";
  return node.children.map(renderInline).join("");
}

function rawText(node: MiniNode): string {
  if (!isEl(node)) return node.value;
  return node.children.map(rawText).join("");
}

function collapseInline(value: string): string {
  return value.replace(/[\t\r\f\v]+/g, " ").replace(/ {2,}/g, " ");
}

function parseMiniHtml(html: string): MiniNode[] {
  const root: MiniEl = { kind: "el", tag: "root", children: [] };
  const stack: MiniEl[] = [root];
  const input = html.replace(/<!--[\s\S]*?-->/g, "");
  let i = 0;

  while (i < input.length) {
    if (input[i] !== "<") {
      const next = input.indexOf("<", i);
      const raw = next === -1 ? input.slice(i) : input.slice(i, next);
      stack[stack.length - 1]!.children.push({ kind: "text", value: decodeEntities(raw) });
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
      stack[stack.length - 1]!.children.push({ kind: "text", value: "<" });
      i += 1;
      continue;
    }
    const tag = tagMatch[1]!.toLowerCase();
    const afterName = i + 1 + tagMatch[1]!.length;
    const tagEnd = findTagEnd(input, afterName);
    const rawTag = input.slice(i, tagEnd === -1 ? input.length : tagEnd + 1);
    const selfClosing = VOID_TAGS.has(tag) || /\/\s*>$/.test(rawTag);
    const href = tag === "a" ? hrefFromTag(rawTag) : undefined;
    i = tagEnd === -1 ? input.length : tagEnd + 1;
    if (tag === "script" || tag === "style") {
      const close = input.toLowerCase().indexOf(`</${tag}`, i);
      i = close === -1 ? input.length : input.indexOf(">", close) + 1;
      continue;
    }
    const el: MiniEl = { kind: "el", tag, href, children: [] };
    stack[stack.length - 1]!.children.push(el);
    if (!selfClosing) stack.push(el);
  }

  return root.children;
}

function hrefFromTag(rawTag: string): string | undefined {
  const match = rawTag.match(/\bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/i);
  const value = match?.[1] ?? match?.[2] ?? match?.[3];
  return value ? decodeEntities(value) : undefined;
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
