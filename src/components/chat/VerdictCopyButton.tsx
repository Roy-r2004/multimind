import { useState } from "react";
import { Check, Copy } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";

type Props = {
  /** Raw Markdown / plain-text verdict source to copy. */
  text: string;
  className?: string;
};

/**
 * Copies the final verdict source text to the clipboard.
 * Shows temporary "Copied" feedback and a toast on success/failure.
 */
export function VerdictCopyButton({ text, className }: Props) {
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      toast.success("Verdict copied");
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error("Could not copy verdict");
    }
  }

  return (
    <button
      type="button"
      aria-label={copied ? "Copied" : "Copy verdict"}
      title={copied ? "Copied" : "Copy verdict to clipboard"}
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
      {copied ? "Copied" : "Copy"}
    </button>
  );
}
