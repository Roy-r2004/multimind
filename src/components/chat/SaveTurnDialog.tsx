import { useEffect, useState } from "react";
import { Loader2, Plus, X } from "lucide-react";
import { toast } from "sonner";
import { Modal } from "@/components/Modal";
import { api } from "@/lib/api";
import type { ApiContentLabel } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

type SaveTurnDialogProps = {
  open: boolean;
  turnId: string | null;
  onClose: () => void;
  onSaved?: () => void;
};

export function SaveTurnDialog({ open, turnId, onClose, onSaved }: SaveTurnDialogProps) {
  const { authHeaders } = useAuth();
  const [name, setName] = useState("");
  const [labels, setLabels] = useState<ApiContentLabel[]>([]);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [newLabel, setNewLabel] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open || !turnId) return;
    const auth = authHeaders();
    if (!auth) return;
    setLoading(true);
    setName("");
    setSelectedIds(new Set());
    setSuggestions([]);
    setNewLabel("");
    void Promise.all([api.contentLabels.list(auth), api.savedDocuments.suggest(auth, turnId)])
      .then(([labelList, suggest]) => {
        setLabels(labelList);
        setName(suggest.name);
        setSuggestions(suggest.label_suggestions);
        const byName = new Map(labelList.map((label) => [label.name.toLowerCase(), label.id]));
        const next = new Set<string>();
        for (const suggestion of suggest.label_suggestions) {
          const id = byName.get(suggestion.toLowerCase());
          if (id) next.add(id);
        }
        setSelectedIds(next);
      })
      .catch((error) => {
        toast.error(error instanceof Error ? error.message : "Could not prepare save dialog");
      })
      .finally(() => setLoading(false));
  }, [open, turnId, authHeaders]);

  async function createLabelInline() {
    const auth = authHeaders();
    if (!auth || !newLabel.trim()) return;
    try {
      const created = await api.contentLabels.create(auth, newLabel.trim());
      setLabels((prev) =>
        prev.some((label) => label.id === created.id)
          ? prev
          : [...prev, created].sort((a, b) => a.name.localeCompare(b.name)),
      );
      setSelectedIds((prev) => new Set(prev).add(created.id));
      setNewLabel("");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not create label");
    }
  }

  async function handleSave() {
    const auth = authHeaders();
    if (!auth || !turnId) return;
    setSaving(true);
    try {
      const suggestionNames = suggestions.filter(
        (suggestion) =>
          !labels.some((label) => label.name.toLowerCase() === suggestion.toLowerCase()) &&
          selectedIds.size === 0,
      );
      await api.savedDocuments.create(auth, {
        turn_id: turnId,
        name: name.trim() || undefined,
        label_ids: [...selectedIds],
        label_names: suggestionNames.length ? suggestionNames.slice(0, 1) : [],
      });
      toast.success("Turn saved");
      onSaved?.();
      onClose();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not save turn");
    } finally {
      setSaving(false);
    }
  }

  function toggleLabel(id: string) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function acceptSuggestion(suggestion: string) {
    const existing = labels.find(
      (label) => label.name.toLowerCase() === suggestion.toLowerCase(),
    );
    if (existing) {
      toggleLabel(existing.id);
      return;
    }
    const auth = authHeaders();
    if (!auth) return;
    try {
      const created = await api.contentLabels.create(auth, suggestion);
      setLabels((prev) => [...prev, created].sort((a, b) => a.name.localeCompare(b.name)));
      setSelectedIds((prev) => new Set(prev).add(created.id));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not create label");
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Save turn as document" size="md">
      {loading ? (
        <div className="flex justify-center py-10">
          <Loader2 className="size-5 animate-spin text-muted-foreground" />
        </div>
      ) : (
        <div className="space-y-4">
          <label className="block space-y-1.5">
            <span className="text-xs font-medium text-muted-foreground">Document name</span>
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Alcohol Consumption Verdict"
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary"
            />
          </label>

          <div className="space-y-2">
            <p className="text-xs font-medium text-muted-foreground">Labels</p>
            <div className="flex flex-wrap gap-1.5">
              {labels.map((label) => {
                const selected = selectedIds.has(label.id);
                return (
                  <button
                    key={label.id}
                    type="button"
                    onClick={() => toggleLabel(label.id)}
                    className={cn(
                      "rounded-full border px-2.5 py-1 text-xs font-medium transition",
                      selected
                        ? "border-primary/40 bg-primary/10 text-primary"
                        : "border-border text-muted-foreground hover:bg-accent",
                    )}
                  >
                    {label.name}
                  </button>
                );
              })}
              {labels.length === 0 && (
                <span className="text-xs text-muted-foreground">No labels yet — create one below.</span>
              )}
            </div>
            {suggestions.length > 0 && (
              <div className="flex flex-wrap items-center gap-1.5 pt-1">
                <span className="text-[11px] text-muted-foreground">Suggested:</span>
                {suggestions.map((suggestion) => (
                  <button
                    key={suggestion}
                    type="button"
                    onClick={() => void acceptSuggestion(suggestion)}
                    className="rounded-full border border-dashed border-border px-2 py-0.5 text-[11px] text-muted-foreground hover:border-primary hover:text-primary"
                  >
                    {suggestion}
                  </button>
                ))}
              </div>
            )}
            <div className="flex gap-2">
              <input
                value={newLabel}
                onChange={(event) => setNewLabel(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.preventDefault();
                    void createLabelInline();
                  }
                }}
                placeholder="New label"
                className="min-w-0 flex-1 rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary"
              />
              <button
                type="button"
                onClick={() => void createLabelInline()}
                disabled={!newLabel.trim()}
                className="inline-flex items-center gap-1 rounded-lg border border-border px-3 py-2 text-xs font-medium hover:bg-accent disabled:opacity-40"
              >
                <Plus className="size-3.5" /> Add
              </button>
            </div>
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              disabled={saving}
              className="inline-flex items-center gap-1 rounded-lg border border-border px-3 py-2 text-sm hover:bg-accent"
            >
              <X className="size-3.5" /> Cancel
            </button>
            <button
              type="button"
              onClick={() => void handleSave()}
              disabled={saving}
              className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            >
              {saving ? <Loader2 className="size-3.5 animate-spin" /> : null}
              Save document
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}
