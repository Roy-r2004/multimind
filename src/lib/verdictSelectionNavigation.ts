import { VERDICT_TABLE_COPY_CONTROL } from "./verdictTableCopy.ts";
import type { SelectionCopyPayload } from "./verdictSelectionCopy.ts";

/** UTF-16 offsets in the Verdict's rendered text, excluding Copy controls. */
export type SelectionLocator = { start: number; end: number; quote: string };
export type SelectionPinPayload = SelectionCopyPayload & { locator: SelectionLocator | null };

function textNodes(root: Element): Text[] {
  const walker = root.ownerDocument.createTreeWalker(root, 4 /* SHOW_TEXT */);
  const nodes: Text[] = [];
  while (walker.nextNode()) {
    const node = walker.currentNode as Text;
    if (!node.parentElement?.closest(VERDICT_TABLE_COPY_CONTROL)) nodes.push(node);
  }
  return nodes;
}

export function captureSelectionLocator(root: Element, range: Range): SelectionLocator | null {
  if (!root.contains(range.startContainer) || !root.contains(range.endContainer)) return null;
  const offset = (container: Node, position: number) => {
    const prefix = root.ownerDocument.createRange();
    prefix.selectNodeContents(root);
    prefix.setEnd(container, position);
    const fragment = prefix.cloneContents();
    fragment.querySelectorAll(VERDICT_TABLE_COPY_CONTROL).forEach((node) => node.remove());
    return fragment.textContent?.length ?? 0;
  };
  const start = offset(range.startContainer, range.startOffset);
  const end = offset(range.endContainer, range.endOffset);
  const quote = textNodes(root)
    .map((node) => node.data)
    .join("")
    .slice(start, end);
  return quote.trim() ? { start, end, quote } : null;
}

/** Match old pins only when unambiguous; never silently choose the wrong occurrence. */
export function findSelectionOffsets(
  text: string,
  selectedText: string,
  locator?: SelectionLocator | null,
): { start: number; end: number } | null {
  if (
    locator &&
    locator.start >= 0 &&
    locator.end > locator.start &&
    text.slice(locator.start, locator.end) === locator.quote
  ) {
    return { start: locator.start, end: locator.end };
  }
  const quote = locator?.quote ?? selectedText;
  if (!quote.trim()) return null;
  // Clipboard list/paragraph whitespace differs from rendered text-node boundaries.
  const positions: number[] = [];
  let compact = "";
  for (let i = 0; i < text.length; i++) {
    if (!/\s/.test(text[i])) {
      compact += text[i];
      positions.push(i);
    }
  }
  const needle = quote.replace(/\s/g, "");
  const start = compact.indexOf(needle);
  if (start < 0 || compact.indexOf(needle, start + 1) >= 0) return null;
  return { start: positions[start], end: positions[start + needle.length - 1] + 1 };
}

export function locateSelectionRange(
  root: Element,
  pin: {
    selectedText?: string | null;
    selectedHtml?: string | null;
    selectionLocator?: SelectionLocator | null;
  },
): Range | null {
  const nodes = textNodes(root);
  const text = nodes.map((node) => node.data).join("");
  let offsets = findSelectionOffsets(text, pin.selectedText ?? "", pin.selectionLocator);
  if (!offsets && !pin.selectionLocator && pin.selectedHtml) {
    // Parse old rich-copy pins as inert text, never insert stored HTML into the page.
    const parsed = new DOMParser().parseFromString(pin.selectedHtml, "text/html");
    parsed.querySelectorAll("script, style").forEach((node) => node.remove());
    offsets = findSelectionOffsets(text, parsed.body.textContent ?? "");
  }
  if (!offsets) return null;
  const range = root.ownerDocument.createRange();
  let position = 0;
  let started = false;
  for (const node of nodes) {
    const end = position + node.length;
    if (!started && offsets.start < end) {
      range.setStart(node, offsets.start - position);
      started = true;
    }
    if (started && offsets.end <= end) {
      range.setEnd(node, offsets.end - position);
      return range;
    }
    position = end;
  }
  return null;
}

export function scrollToSelectionRange(range: Range, thread: HTMLElement | null): void {
  // A pinned table/code fragment may also be inside a horizontal scroller.
  for (
    let parent = range.startContainer.parentElement;
    parent && parent !== thread;
    parent = parent.parentElement
  ) {
    if (
      parent.scrollWidth <= parent.clientWidth ||
      !/(auto|scroll)/.test(getComputedStyle(parent).overflowX)
    )
      continue;
    const fragment = range.getClientRects()[0] ?? range.getBoundingClientRect();
    const bounds = parent.getBoundingClientRect();
    if (fragment.left < bounds.left || fragment.right > bounds.right) {
      parent.scrollLeft += fragment.left - bounds.left;
    }
  }
  const rect = range.getClientRects()[0] ?? range.getBoundingClientRect();
  if (thread) {
    thread.scrollTo({
      top: Math.max(
        0,
        thread.scrollTop + rect.top - thread.getBoundingClientRect().top - thread.clientHeight / 3,
      ),
      behavior: "instant",
    });
  } else {
    window.scrollBy({ top: rect.top - window.innerHeight / 3, behavior: "instant" });
  }
}

function selectedTextRects(range: Range): DOMRect[] {
  const ancestor = range.commonAncestorContainer;
  const nodes = ancestor.nodeType === 3 ? [ancestor as Text] : textNodes(ancestor as Element);
  return nodes.flatMap((node) => {
    if (!range.intersectsNode(node)) return [];
    const selected = node.ownerDocument.createRange();
    selected.setStart(node, node === range.startContainer ? range.startOffset : 0);
    selected.setEnd(node, node === range.endContainer ? range.endOffset : node.length);
    return Array.from(selected.getClientRects());
  });
}

/** Paint temporary, pointer-transparent rectangles without changing DOM text or native selection. */
export function highlightSelectionRange(range: Range, thread: HTMLElement | null): () => void {
  const layer = document.createElement("div");
  layer.setAttribute("aria-hidden", "true");
  layer.dataset.selectionPinHighlight = "";
  Object.assign(layer.style, {
    position: "fixed",
    inset: "0",
    pointerEvents: "none",
    zIndex: "40",
  });
  document.body.appendChild(layer);
  const paint = () => {
    layer.replaceChildren();
    if (!range.startContainer.isConnected) return;
    const bounds = thread?.getBoundingClientRect();
    for (const rect of selectedTextRects(range)) {
      const left = Math.max(rect.left, bounds?.left ?? 0);
      const top = Math.max(rect.top, bounds?.top ?? 0);
      const right = Math.min(rect.right, bounds?.right ?? window.innerWidth);
      const bottom = Math.min(rect.bottom, bounds?.bottom ?? window.innerHeight);
      if (right <= left || bottom <= top) continue;
      const mark = document.createElement("div");
      Object.assign(mark.style, {
        position: "absolute",
        left: `${left}px`,
        top: `${top}px`,
        width: `${right - left}px`,
        height: `${bottom - top}px`,
        background: "rgba(251, 191, 36, 0.35)",
        borderRadius: "2px",
      });
      layer.appendChild(mark);
    }
  };
  const cleanup = () => {
    window.clearTimeout(timer);
    window.removeEventListener("scroll", paint, true);
    window.removeEventListener("resize", paint);
    layer.remove();
  };
  const timer = window.setTimeout(cleanup, 1600);
  window.addEventListener("scroll", paint, true);
  window.addEventListener("resize", paint);
  paint();
  return cleanup;
}
