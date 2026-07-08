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
}: {
  open: boolean;
  onClose: () => void;
  title?: string;
  children: ReactNode;
  size?: "sm" | "md" | "lg" | "xl";
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

  return createPortal(
    <div
      className="fixed inset-0 z-[100] grid place-items-center bg-foreground/50 p-4 backdrop-blur-[2px]"
      onClick={onClose}
      role="presentation"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
        className={cn(
          "relative max-h-[min(90vh,720px)] w-full overflow-hidden rounded-2xl border border-border bg-card shadow-2xl animate-fade-up",
          size === "sm" && "max-w-sm",
          size === "md" && "max-w-lg",
          size === "lg" && "max-w-2xl",
          size === "xl" && "max-w-4xl",
        )}
      >
        {title && (
          <div className="flex items-center justify-between border-b border-border px-5 py-3.5">
            <h3 className="text-base font-semibold">{title}</h3>
            <button
              type="button"
              onClick={onClose}
              className="cursor-pointer rounded-md p-1.5 hover:bg-accent"
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
