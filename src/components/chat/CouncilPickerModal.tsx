import { useEffect, useMemo, useState } from "react";
import { Loader2, Search, X } from "lucide-react";
import { Modal } from "@/components/Modal";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import type { ApiModelSearchResult } from "@/lib/api/types";
import { MAX_COUNCIL_MODELS, slugToModelId } from "@/lib/modelIds";
import { useModels } from "@/lib/models";
import type { ModelSet } from "@/lib/mock";
import { cn } from "@/lib/utils";

type Props = {
  open: boolean;
  onClose: () => void;
  currentSet: ModelSet | undefined;
  onSave: (set: ModelSet) => void | Promise<void>;
};

export function CouncilPickerModal({ open, onClose, currentSet, onSave }: Props) {
  const { models, modelById } = useModels();
  const { authHeaders } = useAuth();
  const [picked, setPicked] = useState<string[]>([]);
  const [verdict, setVerdict] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [orResults, setOrResults] = useState<ApiModelSearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setPicked(currentSet?.models.slice(0, MAX_COUNCIL_MODELS) ?? []);
    setVerdict(currentSet?.verdictModel ?? null);
    setQuery("");
    setOrResults([]);
    setError(null);
  }, [open, currentSet]);

  useEffect(() => {
    const auth = authHeaders();
    if (!auth || query.trim().length < 2) {
      setOrResults([]);
      return;
    }
    const handle = window.setTimeout(() => {
      setSearching(true);
      void api.models
        .search(auth, query.trim(), 25)
        .then(setOrResults)
        .catch(() => setOrResults([]))
        .finally(() => setSearching(false));
    }, 250);
    return () => window.clearTimeout(handle);
  }, [query, authHeaders]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q || q.length < 2) {
      return models.slice(0, 24);
    }
    return models
      .filter(
        (m) =>
          m.name.toLowerCase().includes(q) ||
          m.vendor.toLowerCase().includes(q) ||
          (m.openrouter_slug ?? "").toLowerCase().includes(q),
      )
      .slice(0, 40);
  }, [models, query]);

  function toggleCouncil(id: string) {
    setPicked((prev) => {
      if (prev.includes(id)) return prev.filter((x) => x !== id);
      if (prev.length >= MAX_COUNCIL_MODELS) {
        setError(`Choose up to ${MAX_COUNCIL_MODELS} council models.`);
        return prev;
      }
      setError(null);
      return [...prev, id];
    });
  }

  function selectFromSearch(r: ApiModelSearchResult) {
    const existing = models.find((m) => m.openrouter_slug === r.openrouter_slug);
    const id = existing?.id ?? slugToModelId(r.openrouter_slug);
    toggleCouncil(id);
    if (!verdict) setVerdict(id);
    setQuery("");
    setOrResults([]);
  }

  async function save() {
    if (picked.length === 0) {
      setError("Pick at least one council model.");
      return;
    }
    if (picked.length > MAX_COUNCIL_MODELS) {
      setError(`Maximum ${MAX_COUNCIL_MODELS} council models.`);
      return;
    }
    const verdictId = verdict ?? picked[0];
    setSaving(true);
    try {
      const payload: ModelSet = {
        id: currentSet?.id ?? `custom-${Date.now()}`,
        name: currentSet?.name ?? "My Council",
        description: "Custom 3-model council",
        models: picked,
        verdictModel: verdictId,
        strategy: currentSet?.strategy ?? "Synthesize",
        bestFor: "Custom council",
      };
      await onSave(payload);
      onClose();
    } finally {
      setSaving(false);
    }
  }

  if (!open) return null;

  return (
    <Modal open={open} onClose={onClose} title="Choose your council" size="lg">
      <p className="mb-4 text-sm text-muted-foreground">
        Pick up to <strong className="text-foreground">{MAX_COUNCIL_MODELS} models</strong> from the full
        OpenRouter catalog. They answer in parallel; Verdict AI synthesizes the final answer.
      </p>

      <div className="mb-4 flex flex-wrap gap-2">
        {Array.from({ length: MAX_COUNCIL_MODELS }).map((_, i) => {
          const id = picked[i];
          const m = id ? modelById(id) : null;
          return (
            <div
              key={i}
              className={cn(
                "flex min-h-[3rem] min-w-[8rem] flex-1 items-center gap-2 rounded-xl border px-3 py-2 text-sm",
                m ? "border-primary/30 bg-primary/5" : "border-dashed border-border text-muted-foreground",
              )}
            >
              {m ? (
                <>
                  <span className="size-2 shrink-0 rounded-full" style={{ background: m.color }} />
                  <span className="truncate font-medium">{m.name}</span>
                  <button
                    type="button"
                    onClick={() => toggleCouncil(id!)}
                    className="ml-auto text-muted-foreground hover:text-foreground"
                  >
                    <X className="size-3.5" />
                  </button>
                </>
              ) : (
                <span>Slot {i + 1}</span>
              )}
            </div>
          );
        })}
      </div>

      <div className="relative mb-3">
        <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search all OpenRouter models…"
          className="w-full rounded-xl border border-border bg-background py-2.5 pl-10 pr-3 text-sm"
        />
      </div>

      <div className="max-h-56 overflow-y-auto rounded-xl border border-border">
        {searching && (
          <div className="flex items-center gap-2 px-3 py-3 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" /> Searching…
          </div>
        )}
        {(query.trim().length >= 2 ? orResults : filtered).map((item) => {
          const isSearch = "openrouter_slug" in item;
          const slug = isSearch ? item.openrouter_slug : item.openrouter_slug ?? item.id;
          const id = isSearch
            ? (models.find((m) => m.openrouter_slug === item.openrouter_slug)?.id ??
              slugToModelId(item.openrouter_slug))
            : item.id;
          const name = isSearch ? item.name : item.name;
          const selected = picked.includes(id);
          return (
            <button
              key={id}
              type="button"
              disabled={!selected && picked.length >= MAX_COUNCIL_MODELS}
              onClick={() => (isSearch ? selectFromSearch(item as ApiModelSearchResult) : toggleCouncil(id))}
              className={cn(
                "flex w-full items-center justify-between gap-2 border-b border-border px-3 py-2.5 text-left text-sm last:border-0 hover:bg-accent",
                selected && "bg-primary/5",
              )}
            >
              <div className="min-w-0">
                <div className="font-medium">{name}</div>
                <div className="truncate text-xs text-muted-foreground">
                  {isSearch ? item.openrouter_slug : slug}
                </div>
              </div>
              <span className="shrink-0 text-xs text-primary">{selected ? "Selected" : "Add"}</span>
            </button>
          );
        })}
        {!searching && query.trim().length >= 2 && orResults.length === 0 && (
          <div className="px-3 py-4 text-center text-sm text-muted-foreground">No models found.</div>
        )}
      </div>

      {picked.length > 0 && (
        <div className="mt-4">
          <label className="text-sm font-medium">Verdict AI</label>
          <select
            value={verdict ?? picked[0]}
            onChange={(e) => setVerdict(e.target.value)}
            className="mt-1.5 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
          >
            {picked.map((id) => {
              const m = modelById(id);
              return (
                <option key={id} value={id}>
                  {m.name}
                </option>
              );
            })}
          </select>
        </div>
      )}

      {error && <p className="mt-3 text-sm text-destructive">{error}</p>}

      <div className="mt-5 flex justify-end gap-2">
        <button type="button" onClick={onClose} className="rounded-xl border border-border px-4 py-2 text-sm">
          Cancel
        </button>
        <button
          type="button"
          disabled={saving || picked.length === 0}
          onClick={() => void save()}
          className="rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
        >
          {saving ? "Saving…" : "Use these models"}
        </button>
      </div>
    </Modal>
  );
}
