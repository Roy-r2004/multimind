import { useMemo, useState } from "react";
import { Link2 } from "lucide-react";
import { Modal } from "@/components/Modal";
import type { ChatReferencePick } from "@/lib/chatReference";
import type { Chat } from "@/lib/mock";
import { cn } from "@/lib/utils";

export type { ChatReferencePick };

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

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return chats
      .filter((c) => c.id !== currentChatId)
      .filter((c) => !q || c.title.toLowerCase().includes(q));
  }, [chats, currentChatId, query]);

  return (
    <Modal open={open} onClose={onClose} title="Continue from a previous chat" size="lg">
      <div className="space-y-4">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search chats…"
          className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary/50"
        />
        <div className="rounded-xl bg-accent/20 p-3 text-xs text-muted-foreground">
          Pick a prior chat to continue in this conversation. MultiMind transfers a compact
          handoff once on your next message — then this chat continues with its own memory.
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
                  onPick({ chatId: c.id, title: c.title });
                  onClose();
                }}
                className={cn(
                  "flex w-full items-center justify-between rounded-lg border border-border bg-card p-3 text-left hover:border-primary/40",
                )}
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
        <p className="text-xs text-muted-foreground">
          One-time handoff: rolling memory plus a few recent Q&amp;verdict pairs from the selected
          chat.
        </p>
      </div>
    </Modal>
  );
}
