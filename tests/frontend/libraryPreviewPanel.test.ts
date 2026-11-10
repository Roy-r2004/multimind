import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "../..");
const panelSrc = readFileSync(join(root, "src/components/library/LibraryPreviewPanel.tsx"), "utf8");
const rowSrc = readFileSync(join(root, "src/components/library/LibraryItemRow.tsx"), "utf8");
const indexSrc = readFileSync(join(root, "src/routes/library.index.tsx"), "utf8");
const layoutSrc = readFileSync(join(root, "src/lib/libraryPreviewLayout.ts"), "utf8");

test("preview panel fetch guards every resolution against a since-superseded selection", () => {
  // Both the success and error branches of fetchDetail must check the
  // generation before applying their result, or rapidly switching documents
  // could let an older response land after a newer one.
  const matches = panelSrc.match(/generationRef\.current !== generation/g);
  assert.ok(matches && matches.length >= 2, "expected the generation check in both branches");
  assert.match(panelSrc, /const generation = \+\+generationRef\.current;/);
});

test("preview panel only trusts detail once it matches the currently shown summary", () => {
  assert.match(panelSrc, /detail\?\.id === summary\.id \? detail : null/);
});

test("preview panel reuses the same Markdown/table renderer as the document editor's preview", () => {
  assert.match(panelSrc, /<MessageContent variant="verdict" showTableCopy=\{false\}>/);
});

test("preview panel reuses the existing file-preview fallback instead of inventing one", () => {
  assert.match(panelSrc, /libraryFileContentView\(item\)/);
});

test("preview panel never renders an editable control (read-only by construction)", () => {
  assert.doesNotMatch(panelSrc, /<textarea/);
  assert.doesNotMatch(panelSrc, /onChange=/);
});

test("preview panel exposes Open full document, Attach to Chat, and prev/next navigation", () => {
  assert.match(panelSrc, /onClick=\{onOpenFull\}/);
  assert.match(panelSrc, /onClick=\{onAttach\}/);
  assert.match(panelSrc, /onClick=\{\(\) => onNavigate\("prev"\)\}/);
  assert.match(panelSrc, /onClick=\{\(\) => onNavigate\("next"\)\}/);
});

test("row checkbox and action buttons stop propagation so they never open the preview or navigate", () => {
  assert.match(
    rowSrc,
    /onClick=\{\(event\) => \{\s*event\.stopPropagation\(\);\s*onToggleSelect\(event\);/,
  );
  assert.match(
    rowSrc,
    /className="flex shrink-0 items-center gap-0\.5"\s*\n\s*onClick=\{\(event\) => event\.stopPropagation\(\)\}/,
  );
});

test("a plain click on the row opens the preview; only a modified click still navigates", () => {
  assert.match(rowSrc, /onClick=\{\(\) => onOpenPreview\?\.\(item\)\}/);
  assert.match(rowSrc, /if \(onOpenPreview && !modified\) \{/);
  assert.match(rowSrc, /event\.preventDefault\(\);\s*return;/);
});

test("library.index wires preview open/close/navigate and prunes it after every reload", () => {
  assert.match(indexSrc, /function openPreview\(item: ApiLibraryItem\) \{/);
  assert.match(indexSrc, /function closePreview\(\) \{/);
  assert.match(indexSrc, /function navigatePreview\(direction: "prev" \| "next"\) \{/);
  assert.match(
    indexSrc,
    /setPreviewItemId\(\(prev\) => \(prev && nextIds\.includes\(prev\) \? prev : null\)\)/,
  );
});

test("library.index closes the preview and clears selection together on context changes", () => {
  const section = indexSrc.slice(
    indexSrc.indexOf("if (libraryBrowseContextKey(current)"),
    indexSrc.indexOf("setBrowse(next);"),
  );
  assert.match(section, /clearSelection\(\);/);
  assert.match(section, /setPreviewItemId\(null\);/);
});

test("the resize handle is keyboard-accessible (role=separator, tabIndex, onKeyDown)", () => {
  assert.match(indexSrc, /role="separator"/);
  assert.match(indexSrc, /aria-orientation="vertical"/);
  assert.match(indexSrc, /tabIndex=\{0\}/);
  assert.match(indexSrc, /onKeyDown=\{handlePreviewResizeKeyDown\}/);
  assert.match(indexSrc, /onMouseDown=\{startPreviewResize\}/);
});

test("narrow screens fall back to a Sheet instead of a cramped three-column layout", () => {
  assert.match(indexSrc, /useMediaQuery\("\(min-width: 1024px\)"\)/);
  assert.match(indexSrc, /<Sheet\s*\n\s*open=\{mobilePreviewOpen\}/);
  assert.match(indexSrc, /showCloseButton=\{false\}/);
});

test("preview width persistence never touches window/localStorage outside of an SSR guard", () => {
  assert.match(layoutSrc, /typeof window === "undefined"/);
});

test("the resizable split and preview width are restored client-side only, never during SSR", () => {
  assert.match(
    indexSrc,
    /useLayoutEffect\(\(\) => \{\s*setPreviewWidthPct\(readLibraryPreviewWidthPct/,
  );
});
