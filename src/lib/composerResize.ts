/** Pure helpers for the Prompt Builder composer's drag-to-resize handle. */

export function clampComposerHeight(height: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, height));
}

/**
 * The resize handle sits at the top-right of the composer, which is anchored in place while
 * only the bottom edge moves as the composer grows (the box extends downward from the handle).
 * Moving the pointer away from the composer (upward, out of the box) grows it; moving back
 * toward the composer (downward, into its body) shrinks it. Height is a continuous function of
 * the pointer's current position relative to where the drag started, so reversing direction
 * mid-drag reverses the resize smoothly with no snapping.
 */
export function resizeHeightFromDrag(
  startHeight: number,
  startY: number,
  currentY: number,
  min: number,
  max: number,
): number {
  const awayFromComposerDelta = startY - currentY;
  return clampComposerHeight(startHeight + awayFromComposerDelta, min, max);
}

export const COMPOSER_KEYBOARD_RESIZE_STEP_PX = 24;

/** Non-submitting keyboard alternative to dragging, e.g. ArrowUp/ArrowDown while focused. */
export function stepComposerHeight(
  currentHeight: number,
  direction: 1 | -1,
  step: number,
  min: number,
  max: number,
): number {
  return clampComposerHeight(currentHeight + direction * step, min, max);
}
