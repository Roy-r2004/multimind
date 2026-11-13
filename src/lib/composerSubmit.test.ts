import { describe, expect, it } from "vitest";

import { shouldSubmitOnEnter } from "@/lib/composerSubmit";

const base = { key: "Enter", shiftKey: false, isComposing: false, loading: false, text: "hi" };

describe("shouldSubmitOnEnter", () => {
  it("sends on plain Enter with non-empty text", () => {
    expect(shouldSubmitOnEnter(base)).toBe(true);
  });

  it("does not send on Shift+Enter (newline)", () => {
    expect(shouldSubmitOnEnter({ ...base, shiftKey: true })).toBe(false);
  });

  it("does not send on empty or whitespace-only text", () => {
    expect(shouldSubmitOnEnter({ ...base, text: "" })).toBe(false);
    expect(shouldSubmitOnEnter({ ...base, text: "   \n  " })).toBe(false);
  });

  it("does not send while already loading", () => {
    expect(shouldSubmitOnEnter({ ...base, loading: true })).toBe(false);
  });

  it("does not send while an IME composition is in progress", () => {
    expect(shouldSubmitOnEnter({ ...base, isComposing: true })).toBe(false);
  });

  it("ignores non-Enter keys", () => {
    expect(shouldSubmitOnEnter({ ...base, key: "a" })).toBe(false);
  });

  it("handles very long text without treating it as empty", () => {
    const longText = "line\n".repeat(5000);
    expect(shouldSubmitOnEnter({ ...base, text: longText })).toBe(true);
  });
});
