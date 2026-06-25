import { useMemo, useState } from "react";
import { Link2 } from "lucide-react";
import { Modal } from "@/components/Modal";
import type { Chat } from "@/lib/mock";
import { cn } from "@/lib/utils";

export type ChatReferencePick = {
  chatId: string;
  title: string;
  mode: "summary" | "full";
};

export function ChatReferenceModal({
  open,
  onClose,
  chats,
  currentChatId,
  onPick,
}: {
  open: boolean;
  onClose: () => void;
  chats: Chat[];
  currentChatId: string | null;
  onPick: (ref: ChatReferencePick) => void;
}) {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<"summary" | "full">("summary");

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return chats
      .filter((c) => c.id !== currentChatId)
      .filter((c) => !q || c.title.toLowerCase().includes(q));
  }, [chats, currentChatId, query]);

  return (
    <Modal open={open} onClose={onClose} title="Reference a previous chat" size="lg">
      <div className="space-y-4">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search chats…"
          className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary/50"
        />
        <div className="rounded-xl bg-accent/20 p-3 text-xs text-muted-foreground">
          Example: reference <strong className="text-foreground">&quot;Capital of Lebanon&quot;</strong>, then
          ask <strong className="text-foreground">&quot;How many people live there?&quot;</strong> — MultiAI
          keeps the context.
        </div>
        <div className="max-h-48 space-y-1.5 overflow-y-auto">
          {filtered.length === 0 ? (
            <p className="py-4 text-center text-sm text-muted-foreground">No other chats found.</p>
          ) : (
            filtered.map((c) => (
              <button
                key={c.id}
                type="button"
                onClick={() => {
                  onPick({ chatId: c.id, title: c.title, mode });
                  onClose();
                }}
                className="flex w-full items-center justify-between rounded-lg border border-border bg-card p-3 text-left hover:border-primary/40"
              >
                <div>
                  <div className="text-sm font-medium">{c.title}</div>
                  <div className="text-xs text-muted-foreground">{c.updated}</div>
                </div>
                <Link2 className="size-4 text-muted-foreground" />
              </button>
            ))
          )}
        </div>
        <div>
          <div className="mb-2 text-sm font-medium">Reference mode</div>
          <div className="grid grid-cols-2 gap-2">
            {(["summary", "full"] as const).map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => setMode(m)}
                className={cn(
                  "rounded-xl border p-3 text-left text-sm",
                  mode === m ? "border-primary bg-primary/10" : "border-border",
                )}
              >
                <div className="font-medium">
                  {m === "summary" ? "Use summary only" : "Use full previous chat"}
                </div>
                <div className="text-xs text-muted-foreground">
                  {m === "summary" ? "Lighter, focused context." : "Full prior turns included."}
                </div>
              </button>
            ))}
          </div>
        </div>
      </div>
    </Modal>
  );
}
