import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useCallback, useEffect, useState } from "react";
import { Bookmark, ExternalLink, Loader2, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { AppShell } from "@/components/AppShell";
import { GlassCard, PageHeader } from "@/components/cinematic/PageChrome";
import { MessageContent } from "@/components/chat/MessageContent";
import { api } from "@/lib/api";
import type { ApiSavedVerdict } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";
import { useChatStore } from "@/lib/store";
import { useModels } from "@/lib/models";
import {
  removeSavedVerdictBySourceId,
  restoreSavedVerdictItem,
  savedVerdictCardView,
} from "@/lib/savedVerdicts";
import { setVerdictSavedState } from "@/lib/turnRunner";

export const Route = createFileRoute("/saved-verdicts")({
  head: () => ({ meta: [{ title: "Saved Verdicts — MultiAI" }] }),
  component: SavedVerdictsPage,
});

function SavedVerdictsPage() {
  const { authHeaders, isLoading: authLoading } = useAuth();
  const { setActiveChatId } = useChatStore();
  const { modelById } = useModels();
  const navigate = useNavigate();
  const [items, setItems] = useState<ApiSavedVerdict[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [removingIds, setRemovingIds] = useState<Set<string>>(() => new Set());

  const load = useCallback(async () => {
    if (authLoading) return;
    const auth = authHeaders();
    if (!auth) {
      setItems([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      setItems(await api.savedVerdicts.list(auth));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load saved verdicts");
    } finally {
      setLoading(false);
    }
  }, [authHeaders, authLoading]);

  useEffect(() => {
    void load();
  }, [load]);

  async function removeSaved(item: ApiSavedVerdict) {
    const auth = authHeaders();
    if (!auth || removingIds.has(item.id)) return;
    setRemovingIds((prev) => new Set(prev).add(item.id));
    setItems((prev) => removeSavedVerdictBySourceId(prev, item.source_verdict_id));
    setVerdictSavedState(item.source_verdict_id, false);
    try {
      await api.verdicts.unsave(auth, item.source_verdict_id);
      toast.success("Verdict removed from saved items");
    } catch (e) {
      setItems((prev) => restoreSavedVerdictItem(prev, item));
      setVerdictSavedState(item.source_verdict_id, true);
      setError(e instanceof Error ? e.message : "Failed to remove saved verdict");
    } finally {
      setRemovingIds((prev) => {
        const next = new Set(prev);
        next.delete(item.id);
        return next;
      });
    }
  }

  function openOriginalChat(item: ApiSavedVerdict) {
    if (!item.original_chat_exists || !item.source_chat_id) return;
    setActiveChatId(item.source_chat_id);
    void navigate({ to: "/chat" });
  }

  return (
    <AppShell>
      <div className="relative mx-auto max-w-5xl px-6 py-10">
        <PageHeader
          className="animate-fade-up"
          eyebrow="Library"
          title="Saved Verdicts"
          description="Personal snapshots of completed verdicts you bookmarked."
        />

        {loading ? (
          <div className="flex justify-center py-20">
            <Loader2 className="size-6 animate-spin text-muted-foreground" />
          </div>
        ) : error ? (
          <GlassCard className="mt-8 p-8 text-center text-sm text-destructive">{error}</GlassCard>
        ) : items.length === 0 ? (
          <GlassCard className="mt-8 p-10 text-center">
            <Bookmark className="mx-auto size-8 text-muted-foreground" />
            <p className="mt-3 text-sm text-muted-foreground">No saved verdicts yet.</p>
            <Link
              to="/chat"
              className="mt-4 inline-flex items-center rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
            >
              Go to chat
            </Link>
          </GlassCard>
        ) : (
          <div className="mt-8 space-y-4">
            {items.map((item) => {
              const card = savedVerdictCardView(item);
              const model = modelById(card.modelId);
              return (
                <GlassCard key={item.id} className="p-5">
                  <div className="flex flex-wrap items-start gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.2em] text-primary">
                        <Bookmark className="size-3.5 fill-current" />
                        Saved Verdict
                        <span className="rounded-full bg-primary/15 px-2 py-0.5 tracking-normal">
                          {card.strategy}
                        </span>
                      </div>
                      <h2 className="mt-2 text-lg font-semibold">{card.title}</h2>
                      <p className="mt-1 whitespace-pre-wrap text-sm text-muted-foreground">
                        {card.prompt}
                      </p>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                      {card.canOpenOriginalChat ? (
                        <button
                          type="button"
                          onClick={() => openOriginalChat(item)}
                          className="inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 text-xs font-medium hover:bg-accent"
                        >
                          <ExternalLink className="size-3.5" /> Open original chat
                        </button>
                      ) : (
                        <span className="rounded-lg bg-muted px-2.5 py-1.5 text-xs text-muted-foreground">
                          Original chat unavailable
                        </span>
                      )}
                      <button
                        type="button"
                        disabled={removingIds.has(item.id)}
                        onClick={() => void removeSaved(item)}
                        className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium text-destructive hover:bg-destructive/10 disabled:opacity-50"
                      >
                        {removingIds.has(item.id) ? (
                          <Loader2 className="size-3.5 animate-spin" />
                        ) : (
                          <Trash2 className="size-3.5" />
                        )}
                        Remove from saved
                      </button>
                    </div>
                  </div>

                  <div className="mt-4 space-y-3">
                    <MessageContent>{card.verdictText}</MessageContent>
                    {card.verdictReason && (
                      <MessageContent
                        muted
                        className="rounded-lg border border-border/60 bg-muted/20 px-3 py-2.5"
                      >
                        {card.verdictReason}
                      </MessageContent>
                    )}
                  </div>

                  <div className="mt-4 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                    <span>{model.name}</span>
                    <span>·</span>
                    <span>Saved {new Date(card.savedAt).toLocaleString()}</span>
                  </div>
                </GlassCard>
              );
            })}
          </div>
        )}
      </div>
    </AppShell>
  );
}
