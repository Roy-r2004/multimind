import { describe, expect, it } from "vitest";
import { clampComposerHeight, resizeHeightFromDrag, stepComposerHeight } from "./composerResize";

describe("clampComposerHeight", () => {
  it("passes through values inside the range", () => {
    expect(clampComposerHeight(200, 64, 640)).toBe(200);
  });

  it("clamps to the minimum", () => {
    expect(clampComposerHeight(10, 64, 640)).toBe(64);
  });

  it("clamps to the maximum", () => {
    expect(clampComposerHeight(9000, 64, 640)).toBe(640);
  });
});

describe("resizeHeightFromDrag", () => {
  it("does not change height when the pointer has not moved", () => {
    expect(resizeHeightFromDrag(100, 300, 300, 64, 640)).toBe(100);
  });

  it("grows the composer when dragging away from it (upward)", () => {
    expect(resizeHeightFromDrag(100, 300, 260, 64, 640)).toBe(140);
  });

  it("shrinks the composer when dragging toward it (downward)", () => {
    expect(resizeHeightFromDrag(200, 300, 340, 64, 640)).toBe(160);
  });

  it("is continuous and proportional to movement, not a preset jump", () => {
    expect(resizeHeightFromDrag(100, 300, 295, 64, 640)).toBe(105);
    expect(resizeHeightFromDrag(100, 300, 250, 64, 640)).toBe(150);
  });

  it("reverses smoothly when the drag direction reverses mid-gesture", () => {
    // Drag up 80px from a fresh reference point, then back down 30px from that same start.
    const grown = resizeHeightFromDrag(100, 300, 220, 64, 640);
    expect(grown).toBe(180);
    const partiallyReversed = resizeHeightFromDrag(100, 300, 250, 64, 640);
    expect(partiallyReversed).toBe(150);
    expect(partiallyReversed).toBeLessThan(grown);
  });

  it("clamps to the minimum and maximum while dragging", () => {
    expect(resizeHeightFromDrag(100, 300, 2000, 64, 640)).toBe(64);
    expect(resizeHeightFromDrag(100, 300, -2000, 64, 640)).toBe(640);
  });
});

describe("stepComposerHeight", () => {
  it("grows by the step when direction is 1", () => {
    expect(stepComposerHeight(100, 1, 24, 64, 640)).toBe(124);
  });

  it("shrinks by the step when direction is -1", () => {
    expect(stepComposerHeight(100, -1, 24, 64, 640)).toBe(76);
  });

  it("clamps at the bounds", () => {
    expect(stepComposerHeight(70, -1, 24, 64, 640)).toBe(64);
    expect(stepComposerHeight(630, 1, 24, 64, 640)).toBe(640);
  });
});
