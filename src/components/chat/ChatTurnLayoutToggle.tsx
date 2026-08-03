import { Columns2, Rows3 } from "lucide-react";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import type { ChatTurnLayout } from "@/lib/chatTurnLayout";
import { cn } from "@/lib/utils";

type Props = {
  value: ChatTurnLayout;
  onChange: (layout: ChatTurnLayout) => void;
  className?: string;
  /** Compact icon+label control for chat header / shared header. */
  size?: "sm" | "default";
};

/**
 * Segmented Vertical / Horizontal control for council answer card layout.
 */
export function ChatTurnLayoutToggle({
  value,
  onChange,
  className,
  size = "sm",
}: Props) {
  return (
    <ToggleGroup
      type="single"
      value={value}
      onValueChange={(next) => {
        if (next === "vertical" || next === "horizontal") onChange(next);
      }}
      variant="outline"
      size={size}
      aria-label="Answer layout"
      className={cn(
        "rounded-lg border border-border bg-card/70 p-0.5 shadow-sm",
        className,
      )}
    >
      <ToggleGroupItem
        value="vertical"
        aria-label="Vertical layout"
        title="Vertical — stack answers"
        className="gap-1.5 px-2.5 text-xs data-[state=on]:bg-primary/10 data-[state=on]:text-primary data-[state=on]:shadow-sm"
      >
        <Rows3 className="size-3.5" aria-hidden />
        <span className="hidden sm:inline">Horizontal</span>
      </ToggleGroupItem>
      <ToggleGroupItem
        value="horizontal"
        aria-label="Horizontal layout"
        title="Horizontal — answers side by side"
        className="gap-1.5 px-2.5 text-xs data-[state=on]:bg-primary/10 data-[state=on]:text-primary data-[state=on]:shadow-sm"
      >
        <Columns2 className="size-3.5" aria-hidden />
        <span className="hidden sm:inline">Vertical</span>
      </ToggleGroupItem>
    </ToggleGroup>
  );
}
