import {
  captureSelectionLocator,
  type SelectionPinPayload,
} from "@/lib/verdictSelectionNavigation";
import { useEffect, useState, type RefObject } from "react";
import { createPortal } from "react-dom";
import { toast } from "sonner";
import { selectionRangeToClipboard, verdictSelectionRange } from "@/lib/verdictSelectionCopy";

export function VerdictSelectionPin({
  root,
  onPin,
}: {
  root: RefObject<HTMLDivElement | null>;
  onPin: (payload: SelectionPinPayload) => Promise<void>;
}) {
  const [selection, setSelection] = useState<
    (SelectionPinPayload & { x: number; y: number }) | null
  >(null);
  const [pending, setPending] = useState(false);
  useEffect(() => {
    const update = () => {
      const scoped = verdictSelectionRange(window.getSelection());
      if (!scoped || scoped.root !== root.current) {
        setSelection(null);
        return;
      }
      const payload = selectionRangeToClipboard(scoped.range, scoped.root);
      const rect = scoped.range.getBoundingClientRect();
      setSelection(
        payload
          ? {
              ...payload,
              locator: captureSelectionLocator(scoped.root, scoped.range),
              x: Math.max(8, Math.min(rect.left, window.innerWidth - 140)),
              y: Math.max(8, rect.top - 40),
            }
          : null,
      );
    };
    const dismiss = () => setSelection(null);
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") dismiss();
    };
    document.addEventListener("selectionchange", update);
    document.addEventListener("keydown", keydown);
    window.addEventListener("scroll", dismiss, true);
    window.addEventListener("resize", dismiss);
    return () => {
      document.removeEventListener("selectionchange", update);
      document.removeEventListener("keydown", keydown);
      window.removeEventListener("scroll", dismiss, true);
      window.removeEventListener("resize", dismiss);
    };
  }, [root]);
  if (!selection) return null;
  return createPortal(
    <button
      type="button"
      disabled={pending}
      className="fixed z-50 rounded-lg border border-border bg-background px-3 py-2 text-xs shadow-md"
      style={{ left: selection.x, top: selection.y }}
      onPointerDown={(event) => event.preventDefault()}
      onClick={async () => {
        if (pending) return;
        setPending(true);
        try {
          await onPin(selection);
          setSelection(null);
          window.getSelection()?.removeAllRanges();
        } catch (error) {
          toast.error(error instanceof Error ? error.message : "Could not pin selection");
        } finally {
          setPending(false);
        }
      }}
    >
      {pending ? "Pinning…" : "Pin selection"}
    </button>,
    document.body,
  );
}
