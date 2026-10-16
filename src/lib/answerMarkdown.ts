/**
 * Presentation-only Markdown table repair.
 *
 * GitHub-flavored tables are one physical line per row. Models often leave a
 * blank line after the delimiter, or wrap a long cell onto the next line.
 * The header then renders alone and the body falls out as paragraphs that
 * still contain "|", or the wrapped line becomes a shifted row. This repair
 * runs only on the presentation string. Stored answers are never modified.
 */

const MAX_ROW_LINES = 40;

type Fence = { char: "`" | "~"; length: number };

export function presentAnswerMarkdown(source: string): string {
  const lines = source.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
  const out: string[] = [];
  let fence: Fence | null = null;
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    if (fence) {
      out.push(line);
      if (closesFence(line, fence)) fence = null;
      index += 1;
      continue;
    }

    const opened = openFence(line);
    if (opened) {
      fence = opened;
      out.push(line);
      index += 1;
      continue;
    }

    const columns = tableColumns(lines, index);
    if (columns == null) {
      out.push(line);
      index += 1;
      continue;
    }

    out.push(lines[index].trim());
    out.push(lines[index + 1].trim());
    index += 2;

    while (index < lines.length) {
      if (lines[index].trim() === "") {
        const resumed = resumeAfterBlank(lines, index, columns);
        if (resumed == null) break;
        index = resumed;
        continue;
      }
      if (isHardBoundary(lines[index])) break;
      const row = assembleRow(lines, index, columns);
      if (!row) break;
      out.push(row.text);
      index = row.next;
    }
  }

  return out.join("\n");
}

/** A blank line ends a GFM table. Keep scanning when the next text is still a row. */
function resumeAfterBlank(lines: string[], index: number, columns: number): number | null {
  let look = index + 1;
  while (look < lines.length && lines[look].trim() === "") look += 1;
  if (look >= lines.length || isHardBoundary(lines[look])) return null;
  if (tableColumns(lines, look) != null) return null;
  if (!assembleRow(lines, look, columns)) return null;
  return look;
}

function openFence(line: string): Fence | null {
  const match = /^[ \t]{0,3}(`{3,}|~{3,})(.*)$/.exec(line);
  if (!match) return null;
  const marker = match[1];
  const info = match[2];
  if (marker[0] === "`" && info.includes("`")) return null;
  return { char: marker[0] as "`" | "~", length: marker.length };
}

function closesFence(line: string, fence: Fence): boolean {
  const match = /^[ \t]{0,3}(`{3,}|~{3,})[ \t]*$/.exec(line);
  if (!match) return false;
  const marker = match[1];
  return marker[0] === fence.char && marker.length >= fence.length;
}

function tableColumns(lines: string[], index: number): number | null {
  const header = lines[index];
  const delimiter = lines[index + 1];
  if (delimiter == null) return null;
  if (header.trim() === "" || isHardBoundary(header) || isDelimiterRow(header)) return null;
  if (!isDelimiterRow(delimiter)) return null;
  const headerCells = splitCells(header);
  const delimiterCells = splitCells(delimiter);
  if (!headerCells || !delimiterCells) return null;
  if (headerCells.length !== delimiterCells.length || headerCells.length < 1) return null;
  return headerCells.length;
}

function assembleRow(
  lines: string[],
  start: number,
  columns: number,
): { text: string; next: number } | null {
  const first = lines[start];
  if (first.trim() === "" || isHardBoundary(first) || isDelimiterRow(first)) return null;
  const firstCells = splitCells(first);
  if (!firstCells || !looksLikeRowStart(first, firstCells, columns)) return null;
  if (firstCells.length === columns) return { text: first.trim(), next: start + 1 };
  if (firstCells.length > columns) return null;

  const pieces = [first.trim()];
  let cursor = start + 1;
  while (cursor < lines.length && cursor - start < MAX_ROW_LINES) {
    const next = lines[cursor];
    if (next.trim() === "" || isHardBoundary(next) || isDelimiterRow(next)) return null;
    const nextCells = splitCells(next);
    if (nextCells && nextCells.length >= columns) return null;
    pieces.push(next.trim());
    const joined = pieces.join(" ");
    const joinedCells = splitCells(joined);
    cursor += 1;
    if (!joinedCells || joinedCells.length > columns) return null;
    if (joinedCells.length === columns) return { text: joined, next: cursor };
  }
  return null;
}

function looksLikeRowStart(line: string, cells: string[], columns: number): boolean {
  if (line.trim().startsWith("|")) return true;
  if (cells.length === columns) return true;
  return pipeCount(line) >= 2 && cells.length > 1 && cells.length < columns;
}

function isHardBoundary(line: string): boolean {
  const trimmed = line.trim();
  if (/^#{1,6}(?:\s|$)/.test(trimmed)) return true;
  if (/^(?:```+|~~~+)/.test(trimmed)) return true;
  if (/^(?:[-*+]\s|\d{1,9}[.)]\s)/.test(trimmed)) return true;
  if (trimmed.startsWith(">")) return true;
  if (trimmed.startsWith("$$")) return true;
  return false;
}

function isDelimiterRow(line: string): boolean {
  const cells = splitCells(line);
  if (!cells || cells.length < 1) return false;
  return cells.every((cell) => /^:?-{3,}:?$/.test(cell.trim()));
}

/** Split a row on unescaped pipes. Pipes inside inline code do not count. */
function splitCells(line: string): string[] | null {
  const trimmed = line.trim();
  if (!trimmed.includes("|")) return null;
  let masked = "";
  let inCode = false;
  for (let i = 0; i < trimmed.length; i += 1) {
    const ch = trimmed[i];
    if (ch === "`") {
      inCode = !inCode;
      masked += ch;
      continue;
    }
    if (!inCode && ch === "\\" && trimmed[i + 1] === "|") {
      masked += "\\ ";
      i += 1;
      continue;
    }
    if (inCode && ch === "|") {
      masked += " ";
      continue;
    }
    masked += ch;
  }
  if (inCode) return null;

  let parts = masked.split("|");
  if (masked.startsWith("|")) parts = parts.slice(1);
  if (masked.endsWith("|")) parts = parts.slice(0, -1);
  return parts;
}

function pipeCount(line: string): number {
  const cells = splitCells(line);
  if (!cells) return 0;
  const trimmed = line.trim();
  let count = Math.max(0, cells.length - 1);
  if (trimmed.startsWith("|")) count += 1;
  if (trimmed.endsWith("|")) count += 1;
  return count;
}
