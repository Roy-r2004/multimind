import { afterEach, describe, expect, it, vi } from "vitest";

import {
  findTurnScrollTarget,
  findVerdictSynthesisElement,
  scrollThreadToTop,
  scrollThreadToTurn,
} from "@/lib/chatScroll";

type FakeElement = {
  id: string;
  rect: { top: number; height: number };
  children: FakeElement[];
  getBoundingClientRect: () => { top: number; height: number };
};

function el(id: string, top: number, height: number, children: FakeElement[] = []): FakeElement {
  return {
    id,
    rect: { top, height },
    children,
    getBoundingClientRect() {
      return { top: this.rect.top, height: this.rect.height };
    },
  };
}

function withChildren(target: FakeElement, matcher: (c: FakeElement) => boolean): FakeElement {
  (target as unknown as { querySelector: (sel: string) => FakeElement | null }).querySelector = (
    sel: string,
  ) => {
    if (sel.includes("data-verdict-synthesis") || sel.includes('[id^="verdict-"]')) {
      return target.children.find(matcher) ?? null;
    }
    return null;
  };
  return target;
}

function fakeDocument(elements: FakeElement[]) {
  return {
    getElementById: (id: string) => elements.find((e) => e.id === id) ?? null,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("findTurnScrollTarget / findVerdictSynthesisElement", () => {
  it("targets the turn's own verdict anchor, never another turn's", () => {
    const verdictA = el("verdict-va", 400, 300);
    const turnA = withChildren(el("turn-a", 100, 900, [verdictA]), (c) => c.id === "verdict-va");
    const verdictB = el("verdict-vb", 1500, 300);
    const turnB = withChildren(el("turn-b", 1200, 900, [verdictB]), (c) => c.id === "verdict-vb");
    vi.stubGlobal("document", fakeDocument([turnA, turnB]));

    expect(findTurnScrollTarget("a")?.id).toBe("verdict-va");
    expect(findTurnScrollTarget("b")?.id).toBe("verdict-vb");
  });

  it("falls back to the turn's own top when it has no verdict yet", () => {
    const turn = withChildren(el("turn-c", 200, 500, []), () => false);
    vi.stubGlobal("document", fakeDocument([turn]));

    expect(findTurnScrollTarget("c")?.id).toBe("turn-c");
  });

  it("returns null for a turn that isn't rendered", () => {
    vi.stubGlobal("document", fakeDocument([]));
    expect(findTurnScrollTarget("missing")).toBeNull();
  });

  it("findVerdictSynthesisElement still resolves by explicit verdictId", () => {
    const verdict = el("verdict-v1", 50, 50);
    vi.stubGlobal("document", fakeDocument([verdict]));
    expect(findVerdictSynthesisElement("v1", null)?.id).toBe("verdict-v1");
  });
});

describe("scrollThreadToTop", () => {
  it("aligns the target's top to the thread's top, minus its scroll-margin-top", () => {
    vi.stubGlobal("getComputedStyle", () => ({ scrollMarginTop: "112px" }));
    const calls: Array<{ top: number; behavior?: string }> = [];
    const thread = {
      scrollTop: 50,
      getBoundingClientRect: () => ({ top: 0 }),
      scrollTo: (opts: { top: number; behavior?: string }) => calls.push(opts),
    } as unknown as HTMLElement;
    const target = { getBoundingClientRect: () => ({ top: 500 }) } as unknown as HTMLElement;

    scrollThreadToTop(thread, target, "smooth");

    // target is 500px below the thread's viewport top; after subtracting the
    // 112px scroll-margin, the thread should land at scrollTop 50 + 388.
    expect(calls).toEqual([{ top: 438, behavior: "smooth" }]);
  });

  it("never scrolls to a negative position", () => {
    vi.stubGlobal("getComputedStyle", () => ({ scrollMarginTop: "0px" }));
    const calls: Array<{ top: number }> = [];
    const thread = {
      scrollTop: 0,
      getBoundingClientRect: () => ({ top: 0 }),
      scrollTo: (opts: { top: number }) => calls.push(opts),
    } as unknown as HTMLElement;
    // Target already above the thread's top (e.g. a very short preceding turn).
    const target = { getBoundingClientRect: () => ({ top: -40 }) } as unknown as HTMLElement;

    scrollThreadToTop(thread, target);

    expect(calls[0].top).toBe(0);
  });
});

describe("scrollThreadToTurn", () => {
  it("resolves the same destination regardless of which control triggered it", () => {
    const verdict = el("verdict-v9", 700, 200);
    const turn = withChildren(el("turn-x", 300, 900, [verdict]), (c) => c.id === "verdict-v9");
    vi.stubGlobal("document", fakeDocument([turn]));
    vi.stubGlobal("getComputedStyle", () => ({ scrollMarginTop: "0px" }));

    const calls: Array<{ top: number }> = [];
    const thread = {
      scrollTop: 0,
      getBoundingClientRect: () => ({ top: 0 }),
      scrollTo: (opts: { top: number }) => calls.push(opts),
    } as unknown as HTMLElement;

    // Previous/Next and the dropdown all end up calling scrollThreadToTurn
    // with the same turnId, so repeated calls must be identical.
    const first = scrollThreadToTurn(thread, "x", "smooth");
    const second = scrollThreadToTurn(thread, "x", "auto");

    expect(first?.id).toBe("verdict-v9");
    expect(second?.id).toBe("verdict-v9");
    expect(calls).toEqual([
      { top: 700, behavior: "smooth" },
      { top: 700, behavior: "auto" },
    ]);
  });

  it("returns null and does not call scrollTo when the turn isn't rendered", () => {
    vi.stubGlobal("document", fakeDocument([]));
    const calls: unknown[] = [];
    const thread = {
      scrollTop: 0,
      getBoundingClientRect: () => ({ top: 0 }),
      scrollTo: (opts: unknown) => calls.push(opts),
    } as unknown as HTMLElement;

    expect(scrollThreadToTurn(thread, "missing")).toBeNull();
    expect(calls).toEqual([]);
  });
});
