export type RichClipboardContent = {
  plainText: string;
  html?: string | null;
};

function clipboardWrite(items: ClipboardItem[]): Promise<void> {
  return navigator.clipboard.write(items);
}

function canWriteRichHtml(html: string): boolean {
  return (
    html.trim().length > 0 &&
    typeof navigator !== "undefined" &&
    typeof navigator.clipboard?.write === "function" &&
    typeof ClipboardItem === "function"
  );
}

/**
 * Copy plain text plus optional HTML. Falls back to `writeText(plainText)`
 * whenever rich clipboard APIs are missing or `clipboard.write` fails.
 */
export async function copyRichContent({ plainText, html }: RichClipboardContent): Promise<void> {
  if (html != null && canWriteRichHtml(html)) {
    try {
      await clipboardWrite([
        new ClipboardItem({
          "text/plain": new Blob([plainText], { type: "text/plain" }),
          "text/html": new Blob([html], { type: "text/html" }),
        }),
      ]);
      return;
    } catch {
      // Keep Copy working with the original Markdown.
    }
  }
  await navigator.clipboard.writeText(plainText);
}
