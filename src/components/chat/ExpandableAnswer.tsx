import { useEffect, useRef, useState, type ReactNode } from "react";
import { cn } from "@/lib/utils";

/** Matches `max-h-[7.5rem]` used for collapsed council answers. */
const COLLAPSED_MAX_REM = 7.5;

type Props = {
  children: ReactNode;
  /** When false, content is always fully shown and no toggle is offered. */
  collapsible?: boolean;
  expanded: boolean;
  onToggle: () => void;
  className?: string;
};

/**
 * Clamps long council answers and only offers "Read full answer" when the
 * clamped body would actually hide content.
 */
export function ExpandableAnswer({
  children,
  collapsible = true,
  expanded,
  onToggle,
  className,
}: Props) {
  const bodyRef = useRef<HTMLDivElement>(null);
  const [overflows, setOverflows] = useState(false);

  useEffect(() => {
    if (!collapsible) {
      setOverflows(false);
      return;
    }

    const node = bodyRef.current;
    if (!node) return;

    const measure = () => {
      const rootFont =
        Number.parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
      const clampPx = COLLAPSED_MAX_REM * rootFont;
      setOverflows(node.scrollHeight > clampPx + 1);
    };

    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, [children, collapsible]);

  // Only long (overflowing) answers get a control — short ones stay fully visible
  // even when a parent syncs `expanded` (Horizontal mode).
  const showToggle = collapsible && overflows;

  return (
    <div className={cn("relative", className)}>
      <div
        ref={bodyRef}
        className={cn(collapsible && !expanded && "max-h-[7.5rem] overflow-hidden")}
      >
        {children}
      </div>
      {showToggle ? (
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={expanded}
          className="mt-2 text-[11px] font-medium text-primary hover:underline"
        >
          {expanded ? "Show less" : "Read full answer"}
        </button>
      ) : null}
    </div>
  );
}
