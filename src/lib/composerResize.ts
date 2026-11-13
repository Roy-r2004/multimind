/** Pure helpers for the Prompt Builder composer's click-to-toggle / drag-to-resize icon. */

export const COMPOSER_DRAG_CLICK_THRESHOLD_PX = 4;

/** A pointer that moved less than the threshold counts as a click, not a drag. */
export function isWithinClickThreshold(
  totalMovementPx: number,
  threshold: number = COMPOSER_DRAG_CLICK_THRESHOLD_PX,
): boolean {
  return totalMovementPx <= threshold;
}

export function clampComposerHeight(height: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, height));
}
