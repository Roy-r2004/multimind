/** Helpers for Library item content viewing (detail page). */

export type LibraryFileContentView =
  | { kind: "text"; text: string }
  | { kind: "message"; message: string };

const PREVIEW_UNAVAILABLE =
  "Preview is not available for this file. Download it to view it.";
const EMPTY_TEXT = "No readable text was found in this file.";
const FAILED_TEXT = "MultiMind could not extract readable text from this file.";

/** Both documents and uploaded files use `/library/$itemId`. */
export function libraryItemOpensDetail(itemType: string | null | undefined): boolean {
  return itemType === "document" || itemType === "file";
}

/**
 * Map stored extract / excerpt_status into what the file Content section should show.
 * Does not re-extract; uses API fields only.
 */
export function libraryFileContentView(item: {
  excerpt_status?: string | null;
  text_excerpt?: string | null;
}): LibraryFileContentView {
  const status = (item.excerpt_status ?? "").toLowerCase();
  const excerpt = item.text_excerpt?.trim() ? item.text_excerpt : null;

  if (status === "image") {
    return { kind: "message", message: PREVIEW_UNAVAILABLE };
  }
  if (status === "ready") {
    if (excerpt) return { kind: "text", text: excerpt };
    return { kind: "message", message: EMPTY_TEXT };
  }
  if (status === "empty") {
    return { kind: "message", message: EMPTY_TEXT };
  }
  if (status === "failed") {
    return { kind: "message", message: FAILED_TEXT };
  }
  if (excerpt) {
    return { kind: "text", text: excerpt };
  }
  return { kind: "message", message: PREVIEW_UNAVAILABLE };
}
