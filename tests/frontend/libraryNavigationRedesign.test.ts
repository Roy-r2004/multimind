import assert from "node:assert/strict";
import test from "node:test";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { EMPTY_LIBRARY_BROWSE } from "../../src/lib/libraryBrowseMemory.ts";

const root = join(dirname(fileURLToPath(import.meta.url)), "../..");
const indexSrc = readFileSync(join(root, "src/routes/library.index.tsx"), "utf8");

test("the default browse state is All Items, not the old Home dashboard", () => {
  assert.deepEqual(EMPTY_LIBRARY_BROWSE.view, { kind: "all" });
});

test("leaving the list for a document marks the return flag before navigating", () => {
  // rememberBeforeDocument is the single place that marks "expect a return
  // trip"; every navigate-to-detail call site routes through it.
  assert.match(
    indexSrc,
    /const rememberBeforeDocument = useCallback\(\(\) => \{\s*flushLibraryScroll\(\);\s*leaveLibraryRef\.current = true;\s*[\s\S]{0,200}markLibraryReturnExpected\(browserSessionStorage\(\)\);/,
  );
});

test("mounting the list consumes the return flag and branches fresh vs. restore", () => {
  assert.match(
    indexSrc,
    /const returning = consumeLibraryReturnExpected\(browserSessionStorage\(\)\);/,
  );
  assert.match(
    indexSrc,
    /const stored = returning\s*\?\s*readLibraryBrowseState\(browserSessionStorage\(\)\)\s*:\s*\{\s*\.\.\.EMPTY_LIBRARY_BROWSE\s*\};/,
  );
});

test("creating a new document also marks the return flag before navigating to it", () => {
  const createFn = indexSrc.slice(
    indexSrc.indexOf("async function submitCreateDocument"),
    indexSrc.indexOf("async function submitCreateDocument") + 800,
  );
  assert.match(
    createFn,
    /rememberBeforeDocument\(\);\s*await navigate\(\{ to: "\/library\/\$itemId"/,
  );
});

test("the permanent sidebar and home dashboard components are gone", () => {
  assert.doesNotMatch(indexSrc, /LibrarySidebar/);
  assert.doesNotMatch(indexSrc, /LibraryHome/);
  assert.equal(existsSync(join(root, "src/components/library/LibrarySidebar.tsx")), false);
  assert.equal(existsSync(join(root, "src/components/library/LibraryHome.tsx")), false);
});

test("the toolbar wires the nav dropdown, folders menu, and filters menu together", () => {
  assert.match(indexSrc, /LIBRARY_NAV_ITEMS\.map/);
  assert.match(indexSrc, /<LibraryFoldersMenu/);
  assert.match(indexSrc, /<LibraryFiltersMenu/);
});

test("the breadcrumb's home action now targets All Items, not the removed Home view", () => {
  assert.match(indexSrc, /onHome=\{\(\) => updateBrowse\(\{ view: \{ kind: "all" \} \}\)\}/);
});
