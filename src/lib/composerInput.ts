/** Composer text helpers that do not depend on ChatPage state. */

export const TEXTAREA_MAX_HEIGHT_PX = 280;

export function composerDraftStorageKey(chatId: string | null | undefined): string {
  return `multimind:draft:${chatId ?? "new"}`;
}

export function readComposerDraft(storageKey: string): string {
  if (typeof window === "undefined") return "";
  return window.localStorage.getItem(storageKey) ?? "";
}

export function writeComposerDraft(storageKey: string, value: string): void {
  if (typeof window === "undefined") return;
  if (value === "") {
    window.localStorage.removeItem(storageKey);
  } else {
    window.localStorage.setItem(storageKey, value);
  }
}

export function transcriptInsertion(
  current: string,
  transcript: string,
  selectionStart: number | null,
  selectionEnd: number | null,
): { value: string; cursor: number } {
  const hasSelection =
    selectionStart !== null &&
    selectionEnd !== null &&
    selectionStart >= 0 &&
    selectionEnd <= current.length &&
    selectionEnd >= selectionStart;

  if (!hasSelection) {
    const prefix = current.trim() ? `${current}\n\n` : "";
    return { value: `${prefix}${transcript}`, cursor: prefix.length + transcript.length };
  }

  const before = current.slice(0, selectionStart);
  const after = current.slice(selectionEnd);
  const replacing = selectionEnd > selectionStart;
  const beforeNeedsParagraph = before.endsWith(":") && after.trim().length === 0;
  const afterStartsWithBoundary = after.length === 0 || /^[\s.,!?;:)\]}]/.test(after);
  const prefix =
    replacing || before.length === 0 || /\s$/.test(before)
      ? ""
      : beforeNeedsParagraph
        ? "\n\n"
        : " ";
  const suffix = replacing || afterStartsWithBoundary ? "" : " ";
  const insertion = `${prefix}${transcript}${suffix}`;

  return {
    value: `${before}${insertion}${after}`,
    cursor: before.length + prefix.length + transcript.length,
  };
}
