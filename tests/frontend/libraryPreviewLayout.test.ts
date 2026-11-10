import assert from "node:assert/strict";
import test from "node:test";

import {
  clampLibraryPreviewWidthPct,
  libraryPreviewWidthPctFromDrag,
  LIBRARY_PREVIEW_DEFAULT_PCT,
  LIBRARY_PREVIEW_MAX_PCT,
  LIBRARY_PREVIEW_MIN_PCT,
  nextLibraryPreviewWidthPctForKey,
  readLibraryPreviewWidthPct,
  writeLibraryPreviewWidthPct,
} from "../../src/lib/libraryPreviewLayout.ts";

function memoryStorage(): Storage {
  const data = new Map<string, string>();
  return {
    get length() {
      return data.size;
    },
    clear: () => data.clear(),
    getItem: (key) => data.get(key) ?? null,
    key: (index) => [...data.keys()][index] ?? null,
    removeItem: (key) => {
      data.delete(key);
    },
    setItem: (key, value) => {
      data.set(key, value);
    },
  };
}

test("clampLibraryPreviewWidthPct keeps values within range and falls back for non-finite input", () => {
  assert.equal(clampLibraryPreviewWidthPct(1), LIBRARY_PREVIEW_MIN_PCT);
  assert.equal(clampLibraryPreviewWidthPct(1000), LIBRARY_PREVIEW_MAX_PCT);
  assert.equal(clampLibraryPreviewWidthPct(40), 40);
  assert.equal(clampLibraryPreviewWidthPct(Number.NaN), LIBRARY_PREVIEW_DEFAULT_PCT);
});

test("read/write round-trips through injected storage and clamps on read", () => {
  const storage = memoryStorage();
  assert.equal(readLibraryPreviewWidthPct(storage), LIBRARY_PREVIEW_DEFAULT_PCT);
  writeLibraryPreviewWidthPct(storage, 45);
  assert.equal(readLibraryPreviewWidthPct(storage), 45);

  storage.setItem("multimind.library.previewWidthPct", "9999");
  assert.equal(readLibraryPreviewWidthPct(storage), LIBRARY_PREVIEW_MAX_PCT);

  storage.setItem("multimind.library.previewWidthPct", "not-a-number");
  assert.equal(readLibraryPreviewWidthPct(storage), LIBRARY_PREVIEW_DEFAULT_PCT);
});

test("read/write are no-ops without storage (SSR-safe)", () => {
  assert.equal(readLibraryPreviewWidthPct(null), LIBRARY_PREVIEW_DEFAULT_PCT);
  assert.doesNotThrow(() => writeLibraryPreviewWidthPct(null, 50));
});

test("nextLibraryPreviewWidthPctForKey maps arrow/home/end keys and ignores others", () => {
  assert.equal(nextLibraryPreviewWidthPctForKey(34, "ArrowLeft"), 36);
  assert.equal(nextLibraryPreviewWidthPctForKey(34, "ArrowRight"), 32);
  assert.equal(nextLibraryPreviewWidthPctForKey(34, "Home"), LIBRARY_PREVIEW_MIN_PCT);
  assert.equal(nextLibraryPreviewWidthPctForKey(34, "End"), LIBRARY_PREVIEW_MAX_PCT);
  assert.equal(nextLibraryPreviewWidthPctForKey(34, "Tab"), null);
});

test("nextLibraryPreviewWidthPctForKey clamps at the boundaries", () => {
  assert.equal(
    nextLibraryPreviewWidthPctForKey(LIBRARY_PREVIEW_MAX_PCT, "ArrowLeft"),
    LIBRARY_PREVIEW_MAX_PCT,
  );
  assert.equal(
    nextLibraryPreviewWidthPctForKey(LIBRARY_PREVIEW_MIN_PCT, "ArrowRight"),
    LIBRARY_PREVIEW_MIN_PCT,
  );
});

test("libraryPreviewWidthPctFromDrag grows the preview when the handle moves left", () => {
  const grown = libraryPreviewWidthPctFromDrag({
    startWidthPct: 34,
    startClientX: 500,
    currentClientX: 400,
    containerWidthPx: 1000,
  });
  assert.equal(grown, 44);
});

test("libraryPreviewWidthPctFromDrag shrinks the preview when the handle moves right", () => {
  const shrunk = libraryPreviewWidthPctFromDrag({
    startWidthPct: 34,
    startClientX: 500,
    currentClientX: 600,
    containerWidthPx: 1000,
  });
  assert.equal(shrunk, 24);
});

test("libraryPreviewWidthPctFromDrag is safe against a zero-width container", () => {
  assert.equal(
    libraryPreviewWidthPctFromDrag({
      startWidthPct: 34,
      startClientX: 0,
      currentClientX: 500,
      containerWidthPx: 0,
    }),
    34,
  );
});
