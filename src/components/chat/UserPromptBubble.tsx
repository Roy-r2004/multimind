import { useEffect, useRef, useState, type ComponentProps, type KeyboardEvent } from "react";
import { BookmarkPlus, Loader2, Pencil } from "lucide-react";
import type { ApiTurnAttachment } from "@/lib/api/types";
import { cn } from "@/lib/utils";
import { appendTranscriptToPrompt, canSubmitEditedPrompt } from "@/lib/promptEdit";
import { SentTurnAttachments } from "@/components/chat/SentTurnAttachments";
import { VoiceRecorderButton } from "@/components/chat/VoiceRecorderButton";

type Props = {
  turnId: string;
  message: string;
  attachments?: ApiTurnAttachment[];
  editable: boolean;
  disabledReason?: string;
  submitting: boolean;
  voiceAuth: ComponentProps<typeof VoiceRecorderButton>["auth"];
  voiceDisabled?: boolean;
  onSubmit: (prompt: string) => void | Promise<void>;
  onSavePrompt?: () => void;
  savePromptDisabledReason?: string;
};

export function UserPromptBubble({
  turnId,
  message,
  attachments = [],
  editable,
  disabledReason,
  submitting,
  voiceAuth,
  voiceDisabled = false,
  onSubmit,
  onSavePrompt,
  savePromptDisabledReason,
}: Props) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(message);
  const [isVoiceActive, setIsVoiceActive] = useState(false);
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
    if (isVoiceActive || !canSubmitEditedPrompt(message, draft, submitting)) return;
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
    const canSave = !isVoiceActive && canSubmitEditedPrompt(message, draft, submitting);
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
          spellCheck={true}
          autoCorrect="on"
          autoCapitalize="sentences"
          aria-label="Edit prompt"
          className="min-h-[5.5rem] w-full resize-y rounded-2xl rounded-br-sm border border-primary/40 bg-primary/90 px-4 py-3 text-sm leading-relaxed text-primary-foreground outline-none ring-offset-background placeholder:text-primary-foreground/60 focus:ring-2 focus:ring-ring disabled:opacity-70"
        />
        <div className="flex flex-wrap items-center gap-2">
          <VoiceRecorderButton
            auth={voiceAuth}
            disabled={voiceDisabled || submitting}
            onTranscript={(result) => {
              setDraft((current) => appendTranscriptToPrompt(current, result.text));
            }}
            onRecordingStateChange={setIsVoiceActive}
          />
          <div className="ml-auto flex gap-2">
            <button
              type="button"
              onClick={cancel}
              disabled={submitting || isVoiceActive}
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
      </div>
    );
  }

  return (
    <div className="flex items-start justify-end gap-2">
      <div className="mt-1 flex shrink-0 flex-col gap-1">
        {onSavePrompt ? (
          <button
            type="button"
            aria-label="Save Prompt"
            title={savePromptDisabledReason ?? "Save Prompt"}
            disabled={Boolean(savePromptDisabledReason)}
            onClick={onSavePrompt}
            className={cn(
              "rounded-lg border border-border bg-card/70 p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground",
              savePromptDisabledReason && "cursor-not-allowed opacity-50",
            )}
          >
            <BookmarkPlus className="size-3.5" />
          </button>
        ) : null}
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
              "rounded-lg border border-border bg-card/70 p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground",
              disabledReason && "cursor-not-allowed opacity-50",
            )}
          >
            <Pencil className="size-3.5" />
          </button>
        ) : null}
      </div>
      <div
        data-turn-prompt={turnId}
        className="max-w-[85%] rounded-2xl rounded-br-sm bg-primary/90 px-4 py-3 text-sm text-primary-foreground shadow-lg shadow-primary/20"
      >
        <p className="whitespace-pre-wrap leading-relaxed">{message}</p>
        <SentTurnAttachments attachments={attachments} />
      </div>
    </div>
  );
}
