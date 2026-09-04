export type LibraryDocumentMode = "edit" | "preview";

export const LIBRARY_DOCUMENT_DEFAULT_MODE: LibraryDocumentMode = "edit";

/**
 * Preview always renders the live editor string, including unsaved changes.
 * It must not fall back to the last saved `content_text`.
 */
export function libraryDocumentPreviewContent(liveContent: string): string {
  return liveContent;
}
