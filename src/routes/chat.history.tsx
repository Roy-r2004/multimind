import { createFileRoute, Link } from "@tanstack/react-router";
import { History, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { PageHeader } from "@/components/cinematic/PageChrome";
import { filterChatsByTitle } from "@/lib/chatHistory";
import { useChatStore } from "@/lib/store";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/chat/history")({
  head: () => ({ meta: [{ title: "Chat history - MultiAI" }] }),
  component: ChatHistoryPage,
});

function ChatHistoryPage() {
  const { chats, activeChatId, projectById, setActiveChatId } = useChatStore();
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => filterChatsByTitle(chats, query), [chats, query]);

  return (
    <AppShell>
      <div className="mx-auto max-w-3xl px-6 py-10">
        <PageHeader
          eyebrow="Chat Council"
          title="Chat history"
          description="All conversations available in your organization, newest activity first."
        />

        <div className="mt-6 flex items-center gap-2 rounded-xl border border-border bg-background px-3 py-2">
          <Search className="size-4 shrink-0 text-muted-foreground" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search by title"
            aria-label="Search chats by title"
            className="w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          />
        </div>

        <div className="mt-4 space-y-1">
          {filtered.length === 0 ? (
            <p className="px-2 py-8 text-center text-sm text-muted-foreground">
              {chats.length === 0 ? "No chats yet" : "No chats match your search"}
            </p>
          ) : (
            filtered.map((chat) => {
              const isActive = chat.id === activeChatId;
              return (
                <Link
                  key={chat.id}
                  to="/chat"
                  onClick={() => setActiveChatId(chat.id)}
                  aria-current={isActive ? "page" : undefined}
                  className={cn(
                    "flex items-start gap-3 rounded-xl border border-transparent px-3 py-3 hover:bg-accent",
                    isActive && "border-border bg-accent/70",
                  )}
                >
                  <History className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                  <span className="min-w-0 flex-1">
                    <span
                      className={cn(
                        "block truncate text-sm",
                        isActive ? "font-medium text-foreground" : "text-foreground/90",
                      )}
                    >
                      {chat.title}
                    </span>
                    {projectById(chat.projectId) ? (
                      <span className="mt-0.5 block truncate text-xs text-muted-foreground">
                        {projectById(chat.projectId)?.name}
                      </span>
                    ) : null}
                    <span className="mt-0.5 block text-[11px] text-muted-foreground">
                      {chat.updated}
                    </span>
                  </span>
                </Link>
              );
            })
          )}
        </div>
      </div>
    </AppShell>
  );
}
