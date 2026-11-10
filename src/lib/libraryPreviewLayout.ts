/** Pure helpers for the Library quick-preview panel's width persistence. */

const LIBRARY_PREVIEW_WIDTH_KEY = "multimind.library.previewWidthPct";

export const LIBRARY_PREVIEW_MIN_PCT = 24;
export const LIBRARY_PREVIEW_MAX_PCT = 60;
export const LIBRARY_PREVIEW_DEFAULT_PCT = 34;
export const LIBRARY_PREVIEW_RESIZE_STEP_PCT = 2;
/** "Expanded reading mode" width — wider than the resizable max, never persisted. */
export const LIBRARY_PREVIEW_EXPANDED_PCT = 72;

export function clampLibraryPreviewWidthPct(value: number): number {
  if (!Number.isFinite(value)) return LIBRARY_PREVIEW_DEFAULT_PCT;
  return Math.min(LIBRARY_PREVIEW_MAX_PCT, Math.max(LIBRARY_PREVIEW_MIN_PCT, value));
}

/** Lazily resolves localStorage; never touches `window` during SSR/render. */
export function librarySafeLocalStorage(): Storage | null {
  try {
    if (typeof window === "undefined") return null;
    return window.localStorage;
  } catch {
    return null;
  }
}

export function readLibraryPreviewWidthPct(storage: Storage | null | undefined): number {
  if (!storage) return LIBRARY_PREVIEW_DEFAULT_PCT;
  try {
    const raw = storage.getItem(LIBRARY_PREVIEW_WIDTH_KEY);
    if (!raw) return LIBRARY_PREVIEW_DEFAULT_PCT;
    return clampLibraryPreviewWidthPct(Number(raw));
  } catch {
    return LIBRARY_PREVIEW_DEFAULT_PCT;
  }
}

export function writeLibraryPreviewWidthPct(
  storage: Storage | null | undefined,
  pct: number,
): void {
  if (!storage) return;
  try {
    storage.setItem(
      LIBRARY_PREVIEW_WIDTH_KEY,
      String(Math.round(clampLibraryPreviewWidthPct(pct))),
    );
  } catch {
    // Private mode or a full quota should not block resizing.
  }
}

/** Keyboard resize of the drag handle: ArrowLeft grows the preview, ArrowRight shrinks it. */
export function nextLibraryPreviewWidthPctForKey(current: number, key: string): number | null {
  if (key === "ArrowLeft")
    return clampLibraryPreviewWidthPct(current + LIBRARY_PREVIEW_RESIZE_STEP_PCT);
  if (key === "ArrowRight")
    return clampLibraryPreviewWidthPct(current - LIBRARY_PREVIEW_RESIZE_STEP_PCT);
  if (key === "Home") return LIBRARY_PREVIEW_MIN_PCT;
  if (key === "End") return LIBRARY_PREVIEW_MAX_PCT;
  return null;
}

/** Converts a horizontal drag delta (list-relative, positive = dragging right) into a new width. */
export function libraryPreviewWidthPctFromDrag(params: {
  startWidthPct: number;
  startClientX: number;
  currentClientX: number;
  containerWidthPx: number;
}): number {
  const { startWidthPct, startClientX, currentClientX, containerWidthPx } = params;
  if (containerWidthPx <= 0) return clampLibraryPreviewWidthPct(startWidthPct);
  // The handle sits left of the preview panel: dragging it left grows the preview.
  const deltaPct = ((startClientX - currentClientX) / containerWidthPx) * 100;
  return clampLibraryPreviewWidthPct(startWidthPct + deltaPct);
}
