import { htmlClipboardToMarkdown } from "./libraryHtmlTable.ts";

export const VERDICT_TABLE_COPY_CONTROL = "[data-verdict-table-copy-control]";

type MdPoint = { offset?: number };

export type MdNodePosition = {
  start?: MdPoint;
  end?: MdPoint;
};

/** Slice the original Verdict Markdown using remark/hast source offsets. */
export function markdownFromNodePosition(
  sourceMarkdown: string,
  position?: MdNodePosition | null,
): string | null {
  const start = position?.start?.offset;
  const end = position?.end?.offset;
  if (
    start == null ||
    end == null ||
    start < 0 ||
    end > sourceMarkdown.length ||
    end <= start
  ) {
    return null;
  }
  return sourceMarkdown.slice(start, end);
}

/** Plain text for one table: exact Markdown when offsets exist, else HTML → GFM. */
export function verdictTablePlainText(
  sourceMarkdown: string,
  position: MdNodePosition | undefined,
  tableOuterHtml: string | undefined,
): string {
  const fromSource = markdownFromNodePosition(sourceMarkdown, position);
  if (fromSource != null && fromSource.trim().length > 0) return fromSource;
  if (tableOuterHtml) {
    const fromHtml = htmlClipboardToMarkdown(tableOuterHtml);
    if (fromHtml) return fromHtml;
  }
  return "";
}

/**
 * Full Verdict Copy HTML: clone the body, drop per-table Copy controls, never
 * mutate the live DOM.
 */
export function getVerdictBodyCopyHtml(el: HTMLElement | null | undefined): string | undefined {
  if (!el) return undefined;
  const clone = el.cloneNode(true) as HTMLElement;
  clone.querySelectorAll(VERDICT_TABLE_COPY_CONTROL).forEach((node) => node.remove());
  return clone.innerHTML;
}
