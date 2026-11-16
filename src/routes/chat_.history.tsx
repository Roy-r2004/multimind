import { createFileRoute, Link } from "@tanstack/react-router";
import { History, Loader2, Search } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { PageHeader } from "@/components/cinematic/PageChrome";
import { api } from "@/lib/api";
import type { ApiChatCursor } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";
import {
  dedupeAppendChats,
  filterChatsByTitle,
  HISTORY_PAGE_SIZE,
  HISTORY_SEARCH_DEBOUNCE_MS,
  mapApiChat,
  reconcileHistoryWithStore,
} from "@/lib/chatHistory";
import type { Chat } from "@/lib/mock";
import { useChatStore } from "@/lib/store";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/chat/history")({
  head: () => ({ meta: [{ title: "Chat history - MultiAI" }] }),
  component: ChatHistoryPage,
});

type Phase = "loading" | "loading-more" | "ready" | "error";

function ChatHistoryPage() {
  const { chats, activeChatId, projectById, setActiveChatId, isApiMode, isLoading } =
    useChatStore();
  const { authHeaders } = useAuth();

  const [queryInput, setQueryInput] = useState("");
  const [activeQuery, setActiveQuery] = useState("");
  const [items, setItems] = useState<Chat[]>([]);
  const [cursor, setCursor] = useState<ApiChatCursor | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [phase, setPhase] = useState<Phase>("loading");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const requestIdRef = useRef(0);

  // Debounce the raw keystroke before it drives a server-side search of the
  // entire stored chat history, not just the pages already loaded.
  useEffect(() => {
    const handle = setTimeout(() => setActiveQuery(queryInput), HISTORY_SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(handle);
  }, [queryInput]);

  const fetchFirstPage = useCallback(
    async (q: string) => {
      if (!isApiMode) return;
      const requestId = ++requestIdRef.current;
      setPhase("loading");
      setErrorMessage(null);
      try {
        const auth = authHeaders();
        if (!auth) return;
        const page = await api.chats.listPage(auth, { limit: HISTORY_PAGE_SIZE, q });
        if (requestId !== requestIdRef.current) return;
        setItems(page.items.map(mapApiChat));
        setCursor(page.next_cursor);
        setHasMore(page.has_more);
        setPhase("ready");
      } catch (e) {
        if (requestId !== requestIdRef.current) return;
        setErrorMessage(e instanceof Error ? e.message : "Failed to load chat history.");
        setPhase("error");
      }
    },
    [isApiMode, authHeaders],
  );

  useEffect(() => {
    void fetchFirstPage(activeQuery);
    // Re-fetch only when the (debounced) search term or auth mode changes —
    // not on every background update to the global chat store, which would
    // undo progressive loading by jumping back to page one.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeQuery, isApiMode]);

  const loadMore = useCallback(async () => {
    if (!isApiMode || !cursor || phase === "loading-more") return;
    const requestId = ++requestIdRef.current;
    setPhase("loading-more");
    setErrorMessage(null);
    try {
      const auth = authHeaders();
      if (!auth) return;
      const page = await api.chats.listPage(auth, {
        limit: HISTORY_PAGE_SIZE,
        cursor,
        q: activeQuery,
      });
      if (requestId !== requestIdRef.current) return;
      setItems((prev) => dedupeAppendChats(prev, page.items.map(mapApiChat)));
      setCursor(page.next_cursor);
      setHasMore(page.has_more);
      setPhase("ready");
    } catch (e) {
      if (requestId !== requestIdRef.current) return;
      setErrorMessage(e instanceof Error ? e.message : "Failed to load more chats.");
      setPhase("error");
    }
  }, [isApiMode, cursor, phase, authHeaders, activeQuery]);

  // Mock/offline mode has no pagination endpoint — the full list already
  // lives in memory, so render it straight from the store, always live.
  const mockVisible = useMemo(
    () => filterChatsByTitle(chats, activeQuery),
    [chats, activeQuery],
  );

  // Keep already-loaded API-mode rows in sync with renames, deletes, and the
  // chat-title SSE stream, all of which land in the global chat store.
  const apiVisible = useMemo(
    () => reconcileHistoryWithStore(items, chats, !isLoading),
    [items, chats, isLoading],
  );

  const visible = isApiMode ? apiVisible : mockVisible;
  const isInitialLoading = isApiMode && phase === "loading" && items.length === 0;

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
            value={queryInput}
            onChange={(e) => setQueryInput(e.target.value)}
            placeholder="Search by title"
            aria-label="Search chats by title"
            className="w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          />
        </div>

        <div className="mt-4 space-y-1">
          {isInitialLoading ? (
            <div className="flex items-center justify-center gap-2 px-2 py-8 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" /> Loading chats…
            </div>
          ) : isApiMode && phase === "error" && items.length === 0 ? (
            <div className="px-2 py-8 text-center text-sm text-muted-foreground">
              <p>{errorMessage ?? "Failed to load chat history."}</p>
              <button
                type="button"
                onClick={() => void fetchFirstPage(activeQuery)}
                className="mt-3 rounded-lg border border-border px-3 py-1.5 text-xs font-medium hover:bg-accent"
              >
                Retry
              </button>
            </div>
          ) : visible.length === 0 ? (
            <p className="px-2 py-8 text-center text-sm text-muted-foreground">
              {activeQuery ? "No chats match your search" : "No chats yet"}
            </p>
          ) : (
            <>
              {visible.map((chat) => {
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
              })}

              {isApiMode ? (
                <div className="px-2 py-4 text-center">
                  {phase === "error" ? (
                    <div className="text-sm text-muted-foreground">
                      <p>{errorMessage ?? "Failed to load more chats."}</p>
                      <button
                        type="button"
                        onClick={() => void loadMore()}
                        className="mt-2 rounded-lg border border-border px-3 py-1.5 text-xs font-medium hover:bg-accent"
                      >
                        Retry
                      </button>
                    </div>
                  ) : hasMore ? (
                    <button
                      type="button"
                      disabled={phase === "loading-more"}
                      onClick={() => void loadMore()}
                      className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-1.5 text-xs font-medium hover:bg-accent disabled:opacity-50"
                    >
                      {phase === "loading-more" ? (
                        <>
                          <Loader2 className="size-3.5 animate-spin" /> Loading…
                        </>
                      ) : (
                        "Load older conversations"
                      )}
                    </button>
                  ) : (
                    <p className="text-xs text-muted-foreground">
                      You&apos;ve reached the oldest conversation.
                    </p>
                  )}
                </div>
              ) : null}
            </>
          )}
        </div>
      </div>
    </AppShell>
  );
}
