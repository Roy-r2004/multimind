import { type ReactNode, useEffect } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

export function Modal({
  open,
  onClose,
  title,
  children,
  size = "md",
  tone = "default",
}: {
  open: boolean;
  onClose: () => void;
  title?: string;
  children: ReactNode;
  size?: "sm" | "md" | "lg" | "xl";
  /** Use dream for Scraping Council surfaces */
  tone?: "default" | "dream";
}) {
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [open]);

  if (!open) return null;

  const dream = tone === "dream";

  return createPortal(
    <div
      className={cn(
        "fixed inset-0 z-[100] grid place-items-center p-4 backdrop-blur-[2px]",
        dream ? "bg-black/65" : "bg-foreground/50",
      )}
      onClick={onClose}
      role="presentation"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
        className={cn(
          "relative max-h-[min(90vh,720px)] w-full overflow-hidden rounded-2xl border shadow-2xl animate-fade-up",
          dream
            ? "border-white/10 bg-[#0b161c] text-[#f7f1e4] shadow-[0_30px_80px_rgba(0,0,0,0.55)]"
            : "border-border bg-card",
          size === "sm" && "max-w-sm",
          size === "md" && "max-w-lg",
          size === "lg" && "max-w-2xl",
          size === "xl" && "max-w-4xl",
        )}
      >
        {title && (
          <div
            className={cn(
              "flex items-center justify-between border-b px-5 py-3.5",
              dream ? "border-white/10" : "border-border",
            )}
          >
            <h3
              className={cn(
                "text-base font-semibold",
                dream && "font-display tracking-tight text-[#f7f1e4]",
              )}
            >
              {title}
            </h3>
            <button
              type="button"
              onClick={onClose}
              className={cn(
                "cursor-pointer rounded-md p-1.5",
                dream ? "text-white/60 hover:bg-white/10 hover:text-[#f7f1e4]" : "hover:bg-accent",
              )}
            >
              <X className="size-4" />
            </button>
          </div>
        )}
        <div className="max-h-[min(75vh,640px)] overflow-y-auto p-5">{children}</div>
      </div>
    </div>,
    document.body,
  );
}
