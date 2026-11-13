import { describe, expect, it } from "vitest";
import {
  COMPOSER_DRAG_CLICK_THRESHOLD_PX,
  clampComposerHeight,
  isWithinClickThreshold,
} from "./composerResize";

describe("isWithinClickThreshold", () => {
  it("treats tiny pointer movement as a click", () => {
    expect(isWithinClickThreshold(0)).toBe(true);
    expect(isWithinClickThreshold(COMPOSER_DRAG_CLICK_THRESHOLD_PX)).toBe(true);
  });

  it("treats movement past the threshold as a drag", () => {
    expect(isWithinClickThreshold(COMPOSER_DRAG_CLICK_THRESHOLD_PX + 1)).toBe(false);
    expect(isWithinClickThreshold(50)).toBe(false);
  });

  it("supports a custom threshold", () => {
    expect(isWithinClickThreshold(10, 20)).toBe(true);
    expect(isWithinClickThreshold(30, 20)).toBe(false);
  });
});

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
