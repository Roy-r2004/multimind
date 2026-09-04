import { useState } from "react";
import { Check, Copy } from "lucide-react";
import { toast } from "sonner";
import { copyRichContent } from "@/lib/richClipboard";
import { cn } from "@/lib/utils";

type Props = {
  /** Raw Markdown / plain-text verdict source to copy. */
  text: string;
  /** Rendered Verdict Markdown HTML captured at click time (not the whole card). */
  getHtml?: () => string | undefined;
  className?: string;
  /** Default button label. */
  label?: string;
  /** Temporary label after a successful copy. */
  copiedLabel?: string;
  successMessage?: string;
  errorMessage?: string;
};

/**
 * Copies the final verdict source text (and rendered HTML when provided).
 * Shows temporary "Copied" feedback and a toast on success/failure.
 */
export function VerdictCopyButton({
  text,
  getHtml,
  className,
  label = "Copy",
  copiedLabel = "Copied",
  successMessage = "Verdict copied",
  errorMessage = "Could not copy verdict",
}: Props) {
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    try {
      await copyRichContent({ plainText: text, html: getHtml?.() });
      setCopied(true);
      toast.success(successMessage);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error(errorMessage);
    }
  }

  return (
    <button
      type="button"
      aria-label={copied ? copiedLabel : label}
      title={copied ? copiedLabel : label}
      onClick={() => void handleCopy()}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-medium transition",
        copied
          ? "border-primary/40 bg-primary/10 text-primary"
          : "border-border bg-background/60 text-muted-foreground hover:bg-accent hover:text-foreground",
        className,
      )}
    >
      {copied ? <Check className="size-3.5" aria-hidden /> : <Copy className="size-3.5" aria-hidden />}
      {copied ? copiedLabel : label}
    </button>
  );
}
