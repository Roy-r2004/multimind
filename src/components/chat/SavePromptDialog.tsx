import { useEffect, useState } from "react";
import { Loader2, Plus, X } from "lucide-react";
import { toast } from "sonner";
import { Modal } from "@/components/Modal";
import { MessageContent } from "@/components/chat/MessageContent";
import { api } from "@/lib/api";
import type { ApiContentLabel } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

type SavePromptDialogProps = {
  open: boolean;
  turnId: string | null;
  initialPromptText: string;
  verdictPreview: string;
  onClose: () => void;
  onSaved?: () => void;
};

export function SavePromptDialog({
  open,
  turnId,
  initialPromptText,
  verdictPreview,
  onClose,
  onSaved,
}: SavePromptDialogProps) {
  const { authHeaders } = useAuth();
  const [title, setTitle] = useState("");
  const [promptText, setPromptText] = useState("");
  const [labels, setLabels] = useState<ApiContentLabel[]>([]);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const [newLabel, setNewLabel] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open || !turnId) return;
    const auth = authHeaders();
    if (!auth) return;
    setLoading(true);
    setTitle("");
    setPromptText(initialPromptText);
    setSelectedIds(new Set());
    setNewLabel("");
    void api.contentLabels
      .list(auth)
      .then((labelList) => setLabels(labelList))
      .catch((error) => {
        toast.error(error instanceof Error ? error.message : "Could not load labels");
      })
      .finally(() => setLoading(false));
  }, [open, turnId, initialPromptText, authHeaders]);

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
    const text = promptText.trim();
    if (!text) {
      toast.error("Prompt text is required");
      return;
    }
    if (!verdictPreview.trim()) {
      toast.error("The verdict must finish before this prompt can be saved.");
      return;
    }
    setSaving(true);
    try {
      await api.savedPrompts.create(auth, {
        turn_id: turnId,
        prompt_text: text,
        title: title.trim() || null,
        label_ids: [...selectedIds],
      });
      toast.success("Prompt saved");
      onSaved?.();
      onClose();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not save prompt");
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

  return (
    <Modal open={open} onClose={onClose} title="Save Prompt" size="md">
      {loading ? (
        <div className="flex justify-center py-10">
          <Loader2 className="size-5 animate-spin text-muted-foreground" />
        </div>
      ) : (
        <div className="space-y-4">
          <label className="block space-y-1.5">
            <span className="text-xs font-medium text-muted-foreground">Title (optional)</span>
            <input
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="Short title"
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary"
            />
          </label>

          <label className="block space-y-1.5">
            <span className="text-xs font-medium text-muted-foreground">Prompt text</span>
            <textarea
              value={promptText}
              onChange={(event) => setPromptText(event.target.value)}
              rows={5}
              className="w-full resize-y rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary"
            />
          </label>

          <div className="space-y-1.5">
            <p className="text-xs font-medium text-muted-foreground">Verdict (read-only)</p>
            <div className="max-h-48 overflow-y-auto rounded-lg border border-border bg-muted/20 px-3 py-2.5">
              {verdictPreview.trim() ? (
                <MessageContent muted compact>
                  {verdictPreview}
                </MessageContent>
              ) : (
                <p className="text-sm text-muted-foreground">
                  The verdict must finish before this prompt can be saved.
                </p>
              )}
            </div>
          </div>

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
              disabled={saving || !promptText.trim() || !verdictPreview.trim()}
              className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            >
              {saving ? <Loader2 className="size-3.5 animate-spin" /> : null}
              Save Prompt
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}
