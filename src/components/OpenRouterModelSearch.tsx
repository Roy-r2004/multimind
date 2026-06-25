import { useEffect, useState } from "react";
import { Loader2, Plus, Search, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import type { ApiModel, ApiModelSearchResult } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";
import { useModels } from "@/lib/models";
import { ModelPill } from "@/components/cinematic/PageChrome";
import { cn } from "@/lib/utils";

function fmtRate(n: number) {
  if (n <= 0) return "—";
  return `$${n.toFixed(4)}/1K`;
}

export function OpenRouterModelSearch({ compact = false }: { compact?: boolean }) {
  const { authHeaders } = useAuth();
  const { models, refresh } = useModels();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<ApiModelSearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [adding, setAdding] = useState<string | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const addedSlugs = new Set(
    models.map((m) => m.openrouter_slug ?? m.id).filter(Boolean),
  );

  useEffect(() => {
    const auth = authHeaders();
    if (!auth || query.trim().length < 2) {
      setResults([]);
      return;
    }

    const handle = window.setTimeout(() => {
      setSearching(true);
      setError(null);
      void api.models
        .search(auth, query.trim())
        .then(setResults)
        .catch((e) => setError(e instanceof Error ? e.message : "Search failed"))
        .finally(() => setSearching(false));
    }, 300);

    return () => window.clearTimeout(handle);
  }, [query, authHeaders]);

  async function addModel(slug: string) {
    const auth = authHeaders();
    if (!auth) return;
    setAdding(slug);
    setError(null);
    try {
      await api.models.add(auth, slug);
      await refresh();
      setQuery("");
      setResults([]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to add model");
    } finally {
      setAdding(null);
    }
  }

  async function removeModel(model: ApiModel) {
    if (!model.is_custom) return;
    const auth = authHeaders();
    if (!auth) return;
    setRemoving(model.id);
    setError(null);
    try {
      await api.models.remove(auth, model.id);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to remove model");
    } finally {
      setRemoving(null);
    }
  }

  return (
    <div className={cn("space-y-4", compact && "space-y-3")}>
      <div className="relative">
        <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search OpenRouter — e.g. claude sonnet 4, gemini 2.5, gpt-4.1"
          className="w-full rounded-xl border border-border bg-background py-2.5 pl-10 pr-4 text-sm outline-none focus:border-primary/50"
        />
        {searching && (
          <Loader2 className="absolute right-3 top-1/2 size-4 -translate-y-1/2 animate-spin text-muted-foreground" />
        )}
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}

      {query.trim().length >= 2 && !searching && results.length === 0 && (
        <p className="text-sm text-muted-foreground">No OpenRouter models match that search.</p>
      )}

      {results.length > 0 && (
        <div className="max-h-64 space-y-2 overflow-y-auto rounded-xl border border-border p-2">
          {results.map((r) => {
            const already = addedSlugs.has(r.openrouter_slug);
            return (
              <div
                key={r.openrouter_slug}
                className="flex items-start justify-between gap-3 rounded-lg px-2 py-2 hover:bg-accent"
              >
                <div className="min-w-0">
                  <div className="font-medium text-sm">{r.name}</div>
                  <div className="truncate font-mono text-xs text-muted-foreground">{r.openrouter_slug}</div>
                  <div className="mt-0.5 text-xs text-muted-foreground">
                    {r.vendor} · in {fmtRate(r.input_per_1k)} · out {fmtRate(r.output_per_1k)}
                  </div>
                </div>
                <button
                  type="button"
                  disabled={already || adding === r.openrouter_slug}
                  onClick={() => void addModel(r.openrouter_slug)}
                  className={cn(
                    "inline-flex shrink-0 items-center gap-1 rounded-lg px-2.5 py-1.5 text-xs font-medium",
                    already
                      ? "border border-border text-muted-foreground"
                      : "bg-primary text-primary-foreground hover:opacity-90",
                  )}
                >
                  {adding === r.openrouter_slug ? (
                    <Loader2 className="size-3.5 animate-spin" />
                  ) : (
                    <Plus className="size-3.5" />
                  )}
                  {already ? "Added" : "Add"}
                </button>
              </div>
            );
          })}
        </div>
      )}

      <div>
        <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Your models ({models.length})
        </div>
        <div className={cn("grid gap-3", compact ? "grid-cols-1" : "sm:grid-cols-2 lg:grid-cols-3")}>
          {models.map((m) => (
            <div key={m.id} className="relative group">
              <ModelPill
                name={m.name}
                vendor={m.vendor}
                color={m.color}
                pricing={m.pricing ?? undefined}
                subtitle={m.openrouter_slug ?? undefined}
              />
              {m.is_custom && (
                <button
                  type="button"
                  title="Remove model"
                  disabled={removing === m.id}
                  onClick={() => void removeModel(m)}
                  className="absolute right-2 top-2 rounded-md p-1 text-muted-foreground opacity-0 transition hover:bg-destructive/15 hover:text-destructive group-hover:opacity-100"
                >
                  {removing === m.id ? (
                    <Loader2 className="size-3.5 animate-spin" />
                  ) : (
                    <Trash2 className="size-3.5" />
                  )}
                </button>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
