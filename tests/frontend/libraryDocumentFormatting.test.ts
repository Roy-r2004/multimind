import assert from "node:assert/strict";
import test from "node:test";

import {
  formatLibraryDocumentBlock,
  formatLibraryDocumentInline,
  formatLibraryDocumentList,
  handleLibraryDocumentListKey,
  insertLibraryDocumentText,
} from "../../src/lib/libraryDocumentFormatting.ts";

const block = (
  text: string,
  kind: "heading" | "checklist" | "indent" | "outdent",
  start = 0,
  end = text.length,
) => formatLibraryDocumentBlock(text, start, end, kind);

function format(text: string, kind: "bullet" | "numbered", start = 0, end = text.length) {
  return formatLibraryDocumentList(text, start, end, kind);
}

test("bullets plain lines and toggles canonical bullets off", () => {
  assert.equal(format("A\nB\nC", "bullet").text, "- A\n- B\n- C");
  assert.equal(format("- A\n* B\n+ C", "bullet").text, "A\nB\nC");
});

test("bullets convert numbered and mixed lines while preserving blanks", () => {
  assert.equal(format("1. A\n2. B", "bullet").text, "- A\n- B");
  assert.equal(format("A\n\n* B\n15. C", "bullet").text, "- A\n\n- B\n- C");
});

test("numbering normalizes, toggles, and recognizes multi-digit markers", () => {
  assert.equal(format("A\nB\nC", "numbered").text, "1. A\n2. B\n3. C");
  assert.equal(format("1. A\n15. B", "numbered").text, "A\nB");
  assert.equal(format("- A\n* B", "numbered").text, "1. A\n2. B");
  assert.equal(format("A\n\n* B\n3. C", "numbered").text, "1. A\n\n2. B\n3. C");
});

test("formatting affects complete selected lines only and selects replacement", () => {
  const source = "before\nA\nB\nafter";
  const edit = format(source, "bullet", 8, 10);
  assert.equal(edit.text, "before\n- A\n- B\nafter");
  assert.equal(edit.text.slice(edit.selectionStart, edit.selectionEnd), "- A\n- B");
});

test("Enter continues bullet and increments numbered items", () => {
  assert.deepEqual(handleLibraryDocumentListKey("- A", 3, 3, "Enter"), {
    text: "- A\n- ",
    selectionStart: 6,
    selectionEnd: 6,
  });
  assert.deepEqual(handleLibraryDocumentListKey("12. A", 5, 5, "Enter"), {
    text: "12. A\n13. ",
    selectionStart: 10,
    selectionEnd: 10,
  });
});

test("Enter on empty list items exits the list", () => {
  assert.deepEqual(handleLibraryDocumentListKey("- ", 2, 2, "Enter"), {
    text: "",
    selectionStart: 0,
    selectionEnd: 0,
  });
  assert.deepEqual(handleLibraryDocumentListKey("3. ", 3, 3, "Enter"), {
    text: "",
    selectionStart: 0,
    selectionEnd: 0,
  });
});

test("Backspace immediately after an empty marker removes it", () => {
  assert.equal(handleLibraryDocumentListKey("- ", 2, 2, "Backspace")?.text, "");
  assert.equal(handleLibraryDocumentListKey("10. ", 4, 4, "Backspace")?.text, "");
  assert.equal(handleLibraryDocumentListKey("- A", 2, 2, "Backspace"), null);
});

test("bold wraps, unwraps, preserves surrounding text, selection, and supports an empty caret", () => {
  const wrapped = formatLibraryDocumentInline("say hello now", 4, 9, "bold");
  assert.equal(wrapped.text, "say **hello** now");
  assert.equal(wrapped.text.slice(wrapped.selectionStart, wrapped.selectionEnd), "hello");
  assert.equal(
    formatLibraryDocumentInline(wrapped.text, wrapped.selectionStart, wrapped.selectionEnd, "bold")
      .text,
    "say hello now",
  );
  assert.deepEqual(formatLibraryDocumentInline("ab", 1, 1, "bold"), {
    text: "a****b",
    selectionStart: 3,
    selectionEnd: 3,
  });
});

test("italic wraps and unwraps without treating bold syntax as italic", () => {
  const wrapped = formatLibraryDocumentInline("hello", 0, 5, "italic");
  assert.equal(wrapped.text, "*hello*");
  assert.equal(formatLibraryDocumentInline(wrapped.text, 1, 6, "italic").text, "hello");
  assert.equal(formatLibraryDocumentInline("**hello**", 0, 9, "italic").text, "***hello***");
});

test("heading formats complete lines, preserves blanks, normalizes levels, and toggles H2", () => {
  assert.equal(block("Title", "heading").text, "## Title");
  assert.equal(block("A\nB", "heading").text, "## A\n## B");
  assert.equal(block("# A\n\n### B", "heading").text, "## A\n\n## B");
  assert.equal(block("## A\n\n## B", "heading").text, "A\n\nB");
});

test("checklist converts plain, bullet, numbered, mixed, and multi-digit items", () => {
  assert.equal(block("A\nB", "checklist").text, "- [ ] A\n- [ ] B");
  assert.equal(block("- A\n12. B", "checklist").text, "- [ ] A\n- [ ] B");
  assert.equal(
    block("A\n* B\n3. C\n* [x] D", "checklist").text,
    "- [ ] A\n- [ ] B\n- [ ] C\n- [x] D",
  );
});

test("checklist preserves checked state and toggles completely off", () => {
  assert.equal(block("* [x] Done\nTask", "checklist").text, "- [x] Done\n- [ ] Task");
  assert.equal(block("* [ ] A\n* [x] B", "checklist").text, "A\nB");
});

test("checklist recognizes every bullet marker and uppercase checked state but normalizes to hyphens", () => {
  assert.equal(
    block("- [ ] Hyphen\n* [x] Asterisk\n+ [X] Plus\nNew", "checklist").text,
    "- [ ] Hyphen\n- [x] Asterisk\n- [x] Plus\n- [ ] New",
  );
});

test("checklist Enter continues unchecked, exits empty items, and Backspace removes markers", () => {
  assert.equal(
    handleLibraryDocumentListKey("- [ ] Task", 10, 10, "Enter")?.text,
    "- [ ] Task\n- [ ] ",
  );
  assert.equal(
    handleLibraryDocumentListKey("- [x] Done", 10, 10, "Enter")?.text,
    "- [x] Done\n- [ ] ",
  );
  assert.deepEqual(handleLibraryDocumentListKey("- [ ] ", 6, 6, "Enter"), {
    text: "",
    selectionStart: 0,
    selectionEnd: 0,
  });
  assert.equal(handleLibraryDocumentListKey("- [ ] ", 6, 6, "Backspace")?.text, "");
});

test("indent affects only list and checklist lines and handles multiple lines", () => {
  assert.equal(block("- A", "indent").text, "  - A");
  assert.equal(block("1. A", "indent").text, "  1. A");
  assert.equal(block("* [ ] A", "indent").text, "  * [ ] A");
  assert.equal(block("- A\nplain\n* [ ] B", "indent").text, "  - A\nplain\n  * [ ] B");
});

test("outdent removes one two-space level, accepts a tab, and leaves unindented content", () => {
  assert.equal(block("    - A", "outdent").text, "  - A");
  assert.equal(block("  - A", "outdent").text, "- A");
  assert.equal(block("- A", "outdent").text, "- A");
  assert.equal(block("\t* [ ] A", "outdent").text, "* [ ] A");
});

test("nested Enter preserves indentation for bullets, numbering, and checklists", () => {
  assert.equal(handleLibraryDocumentListKey("  - A", 5, 5, "Enter")?.text, "  - A\n  - ");
  assert.equal(handleLibraryDocumentListKey("  12. A", 7, 7, "Enter")?.text, "  12. A\n  13. ");
  assert.equal(
    handleLibraryDocumentListKey("  - [x] A", 9, 9, "Enter")?.text,
    "  - [x] A\n  - [ ] ",
  );
});

test("block formatting leaves text outside the complete selected lines untouched", () => {
  const source = "before\nTask\nafter";
  const edit = block(source, "checklist", 8, 10);
  assert.equal(edit.text, "before\n- [ ] Task\nafter");
  assert.equal(edit.text.slice(edit.selectionStart, edit.selectionEnd), "- [ ] Task");
});

test("insertLibraryDocumentText replaces the selection and parks the caret after", () => {
  const edit = insertLibraryDocumentText("Hello XX world", 6, 8, "YY");
  assert.equal(edit.text, "Hello YY world");
  assert.equal(edit.selectionStart, 8);
  assert.equal(edit.selectionEnd, 8);
});
