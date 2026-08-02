import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Loader2, Pencil } from "lucide-react";
import { cn } from "@/lib/utils";
import { canSubmitEditedPrompt } from "@/lib/promptEdit";

type Props = {
  turnId: string;
  message: string;
  editable: boolean;
  disabledReason?: string;
  submitting: boolean;
  onSubmit: (prompt: string) => void | Promise<void>;
};

export function UserPromptBubble({
  turnId,
  message,
  editable,
  disabledReason,
  submitting,
  onSubmit,
}: Props) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(message);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    if (!editing) setDraft(message);
  }, [editing, message]);

  useEffect(() => {
    if (!editing) return;
    const node = textareaRef.current;
    if (!node) return;
    node.focus();
    node.setSelectionRange(node.value.length, node.value.length);
    node.style.height = "auto";
    node.style.height = `${Math.min(node.scrollHeight, 240)}px`;
  }, [editing]);

  function cancel() {
    if (submitting) return;
    setDraft(message);
    setEditing(false);
  }

  async function save() {
    if (!canSubmitEditedPrompt(message, draft, submitting)) return;
    try {
      await onSubmit(draft.trim());
      setEditing(false);
    } catch {
      // Keep edit mode and draft so the user can retry.
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      cancel();
      return;
    }
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      void save();
    }
  }

  if (editing) {
    const canSave = canSubmitEditedPrompt(message, draft, submitting);
    return (
      <div className="w-full max-w-[min(100%,36rem)] space-y-2">
        <textarea
          ref={textareaRef}
          value={draft}
          disabled={submitting}
          onChange={(event) => {
            setDraft(event.target.value);
            const node = event.target;
            node.style.height = "auto";
            node.style.height = `${Math.min(node.scrollHeight, 240)}px`;
          }}
          onKeyDown={onKeyDown}
          aria-label="Edit prompt"
          className="min-h-[5.5rem] w-full resize-y rounded-2xl rounded-br-sm border border-primary/40 bg-primary/90 px-4 py-3 text-sm leading-relaxed text-primary-foreground outline-none ring-offset-background placeholder:text-primary-foreground/60 focus:ring-2 focus:ring-ring disabled:opacity-70"
        />
        <div className="flex flex-wrap justify-end gap-2">
          <button
            type="button"
            onClick={cancel}
            disabled={submitting}
            className="rounded-lg border border-border bg-card/80 px-3 py-1.5 text-xs font-medium text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => void save()}
            disabled={!canSave}
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground shadow-sm hover:bg-primary/90 disabled:opacity-50"
          >
            {submitting ? <Loader2 className="size-3.5 animate-spin" /> : null}
            {submitting ? "Regenerating…" : "Save and regenerate"}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex items-start justify-end gap-2">
      {editable ? (
        <button
          type="button"
          aria-label="Edit prompt"
          title={disabledReason ?? "Edit prompt"}
          disabled={Boolean(disabledReason)}
          onClick={() => {
            setDraft(message);
            setEditing(true);
          }}
          className={cn(
            "mt-1 rounded-lg border border-border bg-card/70 p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground",
            disabledReason && "cursor-not-allowed opacity-50",
          )}
        >
          <Pencil className="size-3.5" />
        </button>
      ) : null}
      <div
        data-turn-prompt={turnId}
        className="max-w-[85%] rounded-2xl rounded-br-sm bg-primary/90 px-4 py-3 text-sm text-primary-foreground shadow-lg shadow-primary/20"
      >
        <p className="whitespace-pre-wrap leading-relaxed">{message}</p>
      </div>
    </div>
  );
}
