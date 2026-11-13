import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { ChevronDown, ChevronLeft, ChevronRight } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { chatTurnElementId, scrollThreadToTurn } from "@/lib/chatScroll";
import {
  buildTurnNavigatorItems,
  clampTurnNavigatorIndex,
  nextTurnNavigatorIndex,
  previousTurnNavigatorIndex,
  reconcileTurnNavigatorIndex,
  turnNavigatorIndexById,
  type TurnNavigatorItem,
} from "@/lib/turnNavigator";
import type { ApiTurn } from "@/lib/api/types";
import { cn } from "@/lib/utils";

type Props = {
  turns: ApiTurn[];
  threadRef: RefObject<HTMLDivElement | null>;
  className?: string;
};

/**
 * How long to ignore scroll-driven active-turn updates after a programmatic
 * jump. Generous enough to cover a smooth scroll to a turn's Verdict, which
 * can sit much further down than the turn's top.
 */
const SCROLL_SUPPRESS_MS = 1000;

/**
 * Compact "Question X of Y" control for jumping between a chat's prior
 * questions without disturbing scroll restoration, the composer draft, or
 * any council/verdict/debate state. Previews are extracted locally (no AI
 * calls) from a small text prefix, so thousand-line questions stay cheap.
 */
export function TurnNavigator({ turns, threadRef, className }: Props) {
  const items = useMemo(() => buildTurnNavigatorItems(turns), [turns]);
  const [activeTurnId, setActiveTurnId] = useState<string | null>(null);
  const activeTurnIdRef = useRef<string | null>(null);
  const previousItemsRef = useRef<TurnNavigatorItem[]>([]);
  const suppressObserverUntilRef = useRef(0);

  useEffect(() => {
    const previousIndex = turnNavigatorIndexById(previousItemsRef.current, activeTurnIdRef.current);
    const nextIndex = reconcileTurnNavigatorIndex(activeTurnIdRef.current, previousIndex, items);
    previousItemsRef.current = items;
    const nextTurnId = nextIndex >= 0 ? items[nextIndex].turnId : null;
    if (nextTurnId !== activeTurnIdRef.current) {
      activeTurnIdRef.current = nextTurnId;
      setActiveTurnId(nextTurnId);
    }
  }, [items]);

  useEffect(() => {
    const thread = threadRef.current;
    if (!thread || items.length === 0 || typeof IntersectionObserver === "undefined") return;

    const observer = new IntersectionObserver(
      (entries) => {
        if (Date.now() < suppressObserverUntilRef.current) return;
        const topMost = entries
          .filter((entry) => entry.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
        if (!topMost) return;
        const turnId = topMost.target.id.startsWith("turn-") ? topMost.target.id.slice(5) : null;
        if (turnId && turnId !== activeTurnIdRef.current) {
          activeTurnIdRef.current = turnId;
          setActiveTurnId(turnId);
        }
      },
      { root: thread, threshold: 0, rootMargin: "-10% 0px -70% 0px" },
    );
    for (const item of items) {
      const el = document.getElementById(chatTurnElementId(item.turnId));
      if (el) observer.observe(el);
    }
    return () => observer.disconnect();
  }, [items, threadRef]);

  const activeIndex = turnNavigatorIndexById(items, activeTurnId);
  const displayIndex = activeIndex >= 0 ? activeIndex : 0;
  const current = items[displayIndex];

  const goToIndex = useCallback(
    (index: number) => {
      const clamped = clampTurnNavigatorIndex(index, items.length);
      if (clamped < 0) return;
      const item = items[clamped];
      activeTurnIdRef.current = item.turnId;
      setActiveTurnId(item.turnId);
      suppressObserverUntilRef.current = Date.now() + SCROLL_SUPPRESS_MS;
      // Defer past the current paint (e.g. the dropdown's closing animation)
      // so the scroll measures settled layout, even for very long turns.
      requestAnimationFrame(() => {
        const thread = threadRef.current;
        if (thread) {
          scrollThreadToTurn(thread, item.turnId, "smooth");
          return;
        }
        const target = document.getElementById(chatTurnElementId(item.turnId));
        target?.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    },
    [items, threadRef],
  );

  if (!current) return null;

  return (
    <div
      className={cn(
        "flex min-w-0 items-center gap-1 rounded-lg border border-border bg-card/70 px-2 py-1 text-xs shadow-sm",
        className,
      )}
    >
      <button
        type="button"
        aria-label="Previous question"
        disabled={displayIndex <= 0}
        onClick={() => goToIndex(previousTurnNavigatorIndex(displayIndex, items.length))}
        className="shrink-0 rounded-md p-1 text-muted-foreground hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-30"
      >
        <ChevronLeft className="size-3.5" aria-hidden />
      </button>
      <span className="shrink-0 font-medium text-muted-foreground">
        {displayIndex + 1} of {items.length}
      </span>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            aria-label="Jump to a question"
            title={current.preview}
            className="flex min-w-0 max-w-[12rem] items-center gap-1 truncate rounded-md px-1.5 py-0.5 text-foreground hover:bg-accent sm:max-w-[20rem]"
          >
            <span className="min-w-0 truncate">{current.preview}</span>
            <ChevronDown className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="max-h-72 w-72 overflow-y-auto">
          {items.map((item) => (
            <DropdownMenuItem
              key={item.turnId}
              onSelect={() => goToIndex(item.index)}
              className={cn(item.turnId === current.turnId && "bg-accent text-foreground")}
            >
              <span className="w-6 shrink-0 text-muted-foreground">{item.index + 1}.</span>
              <span className="min-w-0 truncate">{item.preview}</span>
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
      <button
        type="button"
        aria-label="Next question"
        disabled={displayIndex >= items.length - 1}
        onClick={() => goToIndex(nextTurnNavigatorIndex(displayIndex, items.length))}
        className="shrink-0 rounded-md p-1 text-muted-foreground hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-30"
      >
        <ChevronRight className="size-3.5" aria-hidden />
      </button>
    </div>
  );
}
