import { useMemo, useState } from "react";
import { Check, Link2 } from "lucide-react";
import { Modal } from "@/components/Modal";
import {
  MAX_CHAT_REFERENCES,
  toggleChatReference,
  type ChatReferencePick,
} from "@/lib/chatReference";
import type { Chat } from "@/lib/mock";
import { cn } from "@/lib/utils";

export type { ChatReferencePick };

export function ChatReferenceModal({
  open,
  onClose,
  chats,
  currentChatId,
  selected,
  onChange,
}: {
  open: boolean;
  onClose: () => void;
  chats: Chat[];
  currentChatId: string | null;
  selected: ChatReferencePick[];
  onChange: (refs: ChatReferencePick[]) => void;
}) {
  const [query, setQuery] = useState("");
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return chats
      .filter((chat) => chat.id !== currentChatId)
      .filter((chat) => !q || chat.title.toLowerCase().includes(q));
  }, [chats, currentChatId, query]);

  return (
    <Modal open={open} onClose={onClose} title="Continue from previous chats" size="lg">
      <div className="space-y-4">
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search chats…"
          className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary/50"
        />
        <div className="flex items-center justify-between rounded-xl bg-accent/20 p-3 text-xs text-muted-foreground">
          <span>Choose up to two prior chats. One chat keeps the existing one-time handoff.</span>
          <span className="ml-3 shrink-0 font-medium text-foreground">{selected.length}/2</span>
        </div>
        <div className="max-h-48 space-y-1.5 overflow-y-auto">
          {filtered.length === 0 ? (
            <p className="py-4 text-center text-sm text-muted-foreground">No other chats found.</p>
          ) : (
            filtered.map((chat) => {
              const isSelected = selected.some((item) => item.chatId === chat.id);
              const disabled = selected.length >= MAX_CHAT_REFERENCES && !isSelected;
              return (
                <button
                  key={chat.id}
                  type="button"
                  disabled={disabled}
                  aria-pressed={isSelected}
                  onClick={() =>
                    onChange(toggleChatReference(selected, { chatId: chat.id, title: chat.title }))
                  }
                  className={cn(
                    "flex w-full items-center justify-between rounded-lg border bg-card p-3 text-left",
                    isSelected
                      ? "border-primary/60 bg-primary/10"
                      : "border-border hover:border-primary/40",
                    disabled && "cursor-not-allowed opacity-45",
                  )}
                >
                  <div>
                    <div className="text-sm font-medium">{chat.title}</div>
                    <div className="text-xs text-muted-foreground">{chat.updated}</div>
                  </div>
                  {isSelected ? (
                    <Check className="size-4 text-primary" />
                  ) : (
                    <Link2 className="size-4 text-muted-foreground" />
                  )}
                </button>
              );
            })
          )}
        </div>
        {selected.length >= MAX_CHAT_REFERENCES && (
          <p className="text-xs text-muted-foreground">
            Remove a selected chat before choosing another.
          </p>
        )}
        <div className="flex items-center justify-between gap-3">
          <button
            type="button"
            onClick={() => onChange([])}
            disabled={selected.length === 0}
            className="text-sm text-muted-foreground hover:text-foreground disabled:opacity-40"
          >
            Clear
          </button>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
          >
            Done
          </button>
        </div>
      </div>
    </Modal>
  );
}
