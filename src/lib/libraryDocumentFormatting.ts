export type LibraryListKind = "bullet" | "numbered";
export type LibraryBlockKind = LibraryListKind | "heading" | "checklist" | "indent" | "outdent";

export type TextareaEdit = { text: string; selectionStart: number; selectionEnd: number };

const CHECKLIST_RE = /^(\s*)[*+-]\s+\[([ xX])\]\s+(.*)$/;
const BULLET_RE = /^(\s*)[-*+]\s+(?!\[[ xX]\]\s)(.*)$/;
const NUMBERED_RE = /^(\s*)\d+\.\s+(.*)$/;
const HEADING_RE = /^(\s*)#{1,6}\s+(.*)$/;

function listMatch(line: string) {
  return line.match(CHECKLIST_RE) ?? line.match(BULLET_RE) ?? line.match(NUMBERED_RE);
}

function listContent(line: string) {
  const checklist = line.match(CHECKLIST_RE);
  if (checklist)
    return {
      indent: checklist[1],
      content: checklist[3],
      checked: checklist[2].toLowerCase() === "x",
    };
  const match = line.match(BULLET_RE) ?? line.match(NUMBERED_RE);
  if (match) return { indent: match[1], content: match[2], checked: false };
  const indent = line.match(/^\s*/)?.[0] ?? "";
  return { indent, content: line.slice(indent.length), checked: false };
}

function lineRange(text: string, selectionStart: number, selectionEnd: number) {
  const start = text.lastIndexOf("\n", Math.max(0, selectionStart - 1)) + 1;
  const effectiveEnd =
    selectionEnd > selectionStart && text[selectionEnd - 1] === "\n"
      ? selectionEnd - 1
      : selectionEnd;
  const nextNewline = text.indexOf("\n", effectiveEnd);
  return { start, end: nextNewline === -1 ? text.length : nextNewline };
}

/** Toggle an exact selection using canonical Markdown emphasis markers. */
export function formatLibraryDocumentInline(
  text: string,
  selectionStart: number,
  selectionEnd: number,
  kind: "bold" | "italic",
): TextareaEdit {
  const marker = kind === "bold" ? "**" : "*";
  const selected = text.slice(selectionStart, selectionEnd);
  const selectedWrapped =
    selected.startsWith(marker) &&
    selected.endsWith(marker) &&
    selected.length >= marker.length * 2;
  const surrounded =
    text.slice(selectionStart - marker.length, selectionStart) === marker &&
    text.slice(selectionEnd, selectionEnd + marker.length) === marker &&
    !(kind === "italic" && (text[selectionStart - 2] === "*" || text[selectionEnd + 1] === "*"));

  if (selectedWrapped && !(kind === "italic" && selected.startsWith("**"))) {
    const inner = selected.slice(marker.length, -marker.length);
    return {
      text: text.slice(0, selectionStart) + inner + text.slice(selectionEnd),
      selectionStart,
      selectionEnd: selectionStart + inner.length,
    };
  }
  if (surrounded) {
    return {
      text:
        text.slice(0, selectionStart - marker.length) +
        selected +
        text.slice(selectionEnd + marker.length),
      selectionStart: selectionStart - marker.length,
      selectionEnd: selectionEnd - marker.length,
    };
  }
  return {
    text: text.slice(0, selectionStart) + marker + selected + marker + text.slice(selectionEnd),
    selectionStart: selectionStart + marker.length,
    selectionEnd: selectionStart + marker.length + selected.length,
  };
}

/** Toggle or normalize complete selected lines using canonical Markdown block syntax. */
export function formatLibraryDocumentBlock(
  text: string,
  selectionStart: number,
  selectionEnd: number,
  kind: LibraryBlockKind,
): TextareaEdit {
  if (kind === "bullet" || kind === "numbered")
    return formatLibraryDocumentList(text, selectionStart, selectionEnd, kind);
  const range = lineRange(text, selectionStart, selectionEnd);
  const lines = text.slice(range.start, range.end).split("\n");
  const nonblank = lines.filter((line) => line.trim().length > 0);
  const toggleOff =
    kind === "heading"
      ? nonblank.length > 0 && nonblank.every((line) => /^(\s*)##\s+/.test(line))
      : kind === "checklist" &&
        nonblank.length > 0 &&
        nonblank.every((line) => CHECKLIST_RE.test(line));

  const replacement = lines
    .map((line) => {
      if (!line.trim()) return line;
      if (kind === "heading") {
        const match = line.match(HEADING_RE);
        const indent = match?.[1] ?? line.match(/^\s*/)?.[0] ?? "";
        const content = match?.[2] ?? line.slice(indent.length);
        return toggleOff ? `${indent}${content}` : `${indent}## ${content}`;
      }
      if (kind === "checklist") {
        const item = listContent(line);
        return toggleOff
          ? `${item.indent}${item.content}`
          : `${item.indent}- [${item.checked ? "x" : " "}] ${item.content}`;
      }
      if (kind === "indent") return listMatch(line) ? `  ${line}` : line;
      if (line.startsWith("\t")) return line.slice(1);
      if (line.startsWith("  ")) return line.slice(2);
      return line;
    })
    .join("\n");

  return {
    text: text.slice(0, range.start) + replacement + text.slice(range.end),
    selectionStart: range.start,
    selectionEnd: range.start + replacement.length,
  };
}

/** Toggle or normalize complete selected lines using canonical Markdown list markers. */
export function formatLibraryDocumentList(
  text: string,
  selectionStart: number,
  selectionEnd: number,
  kind: LibraryListKind,
): TextareaEdit {
  const range = lineRange(text, selectionStart, selectionEnd);
  const lines = text.slice(range.start, range.end).split("\n");
  const nonblank = lines.filter((line) => line.trim().length > 0);
  const regex = kind === "bullet" ? BULLET_RE : NUMBERED_RE;
  const toggleOff = nonblank.length > 0 && nonblank.every((line) => regex.test(line));
  let number = 0;
  const replacement = lines
    .map((line) => {
      if (!line.trim()) return line;
      const item = listContent(line);
      if (toggleOff) return `${item.indent}${item.content}`;
      if (kind === "bullet") return `${item.indent}- ${item.content}`;
      number += 1;
      return `${item.indent}${number}. ${item.content}`;
    })
    .join("\n");
  return {
    text: text.slice(0, range.start) + replacement + text.slice(range.end),
    selectionStart: range.start,
    selectionEnd: range.start + replacement.length,
  };
}

/** Handle list-aware textarea keyboard behavior, including checklists and indentation. */
export function handleLibraryDocumentListKey(
  text: string,
  selectionStart: number,
  selectionEnd: number,
  key: "Enter" | "Backspace",
): TextareaEdit | null {
  if (selectionStart !== selectionEnd) return null;
  const start = text.lastIndexOf("\n", Math.max(0, selectionStart - 1)) + 1;
  const newline = text.indexOf("\n", selectionStart);
  const end = newline === -1 ? text.length : newline;
  const line = text.slice(start, end);
  const checklist = line.match(/^(\s*)[*+-]\s+\[[ xX]\]\s(.*)$/);
  const bullet = line.match(/^(\s*)[-*+]\s(?!\[[ xX]\]\s)(.*)$/);
  const numbered = line.match(/^(\s*)(\d+)\.\s(.*)$/);
  if (!checklist && !bullet && !numbered) return null;
  const indent = (checklist ?? bullet ?? numbered)![1];
  const content = checklist?.[2] ?? bullet?.[2] ?? numbered?.[3] ?? "";
  const marker = checklist ? "- [ ] " : bullet ? "- " : `${Number(numbered![2]) + 1}. `;
  const markerLength = checklist
    ? line.length - content.length - indent.length
    : bullet
      ? 2
      : numbered![2].length + 2;

  if (key === "Backspace") {
    if (selectionStart !== start + indent.length + markerLength || content.length > 0) return null;
    const nextText = text.slice(0, start) + indent + text.slice(end);
    const caret = start + indent.length;
    return { text: nextText, selectionStart: caret, selectionEnd: caret };
  }
  if (!content.trim()) {
    const nextText = text.slice(0, start) + indent + text.slice(end);
    const caret = start + indent.length;
    return { text: nextText, selectionStart: caret, selectionEnd: caret };
  }
  const insertion = `\n${indent}${marker}`;
  const nextText = text.slice(0, selectionStart) + insertion + text.slice(selectionStart);
  const caret = selectionStart + insertion.length;
  return { text: nextText, selectionStart: caret, selectionEnd: caret };
}
