import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { Loader2, Send, Square } from "lucide-react";
import { composerValueAfterStop } from "@/lib/chatStop";
import {
  TEXTAREA_MAX_HEIGHT_PX,
  readComposerDraft,
  transcriptInsertion,
  writeComposerDraft,
} from "@/lib/composerInput";

export type ChatComposerHandle = {
  getValue: () => string;
  setValue: (value: string) => void;
  replaceValue: (value: string) => void;
  insertTranscript: (transcript: string) => boolean;
  restoreAfterStop: (stoppedPrompt: string) => void;
  focus: () => void;
};

type ChatComposerProps = {
  draftStorageKey: string;
  disabled: boolean;
  placeholder: string;
  /** All send blockers except empty composer text (checked locally). */
  submitBlocked: boolean;
  showStop: boolean;
  stopBusy: boolean;
  onSend: () => void;
  onStop: () => void;
  startActions: ReactNode;
  endActions: ReactNode;
};

export const ChatComposer = forwardRef<ChatComposerHandle, ChatComposerProps>(function ChatComposer(
  {
    draftStorageKey,
    disabled,
    placeholder,
    submitBlocked,
    showStop,
    stopBusy,
    onSend,
    onStop,
    startActions,
    endActions,
  },
  ref,
) {
  const [input, setInput] = useState("");
  const inputRef = useRef(input);
  const draftStorageKeyRef = useRef(draftStorageKey);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  inputRef.current = input;
  draftStorageKeyRef.current = draftStorageKey;

  const persistValue = useCallback((value: string) => {
    setInput(value);
    writeComposerDraft(draftStorageKeyRef.current, value);
  }, []);

  useEffect(() => {
    setInput(readComposerDraft(draftStorageKey));
  }, [draftStorageKey]);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, TEXTAREA_MAX_HEIGHT_PX)}px`;
  }, [input]);

  useImperativeHandle(
    ref,
    () => ({
      getValue: () => inputRef.current,
      setValue: (value: string) => {
        persistValue(value);
      },
      replaceValue: (value: string) => {
        setInput(value);
      },
      insertTranscript: (transcriptValue: string) => {
        const transcript = transcriptValue.trim();
        if (!transcript) return false;

        const textarea = textareaRef.current;
        const selectionStart = textarea ? textarea.selectionStart : null;
        const selectionEnd = textarea ? textarea.selectionEnd : null;
        let cursorPosition: number | null = null;

        setInput((current) => {
          const next = transcriptInsertion(current, transcript, selectionStart, selectionEnd);
          cursorPosition = next.cursor;
          writeComposerDraft(draftStorageKeyRef.current, next.value);
          return next.value;
        });

        if (typeof window !== "undefined") {
          window.requestAnimationFrame(() => {
            const currentTextarea = textareaRef.current;
            if (!currentTextarea || cursorPosition === null) return;
            currentTextarea.focus();
            currentTextarea.setSelectionRange(cursorPosition, cursorPosition);
          });
        }
        return true;
      },
      restoreAfterStop: (stoppedPrompt: string) => {
        const restored = composerValueAfterStop(inputRef.current, stoppedPrompt);
        if (restored !== inputRef.current) {
          persistValue(restored);
        }
      },
      focus: () => {
        textareaRef.current?.focus();
      },
    }),
    [persistValue],
  );

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      if (!input.trim() || submitBlocked) return;
      onSend();
    }
  }

  return (
    <div className="rounded-[1.35rem] border border-border/90 bg-card/95 shadow-[0_8px_28px_oklch(0.45_0.04_240/0.08)] ring-1 ring-primary/5">
      <textarea
        ref={textareaRef}
        value={input}
        onChange={(event) => persistValue(event.target.value)}
        onKeyDown={onKeyDown}
        rows={2}
        disabled={disabled}
        spellCheck={true}
        autoCorrect="on"
        autoCapitalize="sentences"
        placeholder={placeholder}
        className="block max-h-[280px] min-h-[3.5rem] w-full resize-none overflow-y-auto rounded-[1.35rem] bg-transparent px-4 pt-3 pb-2 text-lg outline-none placeholder:text-muted-foreground disabled:opacity-50"
      />
      <div className="flex flex-wrap items-center gap-1 px-2 pb-2">
        {startActions}
        <div className="ml-auto flex min-w-0 flex-wrap items-center justify-end gap-1">
          {endActions}
          {showStop ? (
            <button
              type="button"
              onClick={onStop}
              disabled={stopBusy}
              aria-label="Stop generating"
              className="inline-flex items-center gap-2 rounded-xl bg-destructive px-3.5 py-2 text-sm font-medium text-destructive-foreground shadow-sm hover:bg-destructive/90 disabled:opacity-40"
            >
              {stopBusy ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <Square className="size-3.5 fill-current" />
              )}
              {stopBusy ? "Stopping..." : "Stop generating"}
            </button>
          ) : (
            <button
              type="button"
              onClick={onSend}
              disabled={!input.trim() || submitBlocked}
              className="inline-flex items-center gap-2 rounded-xl bg-primary px-3.5 py-2 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90 disabled:opacity-40"
            >
              <Send className="size-3.5" />
              Send
            </button>
          )}
        </div>
      </div>
    </div>
  );
});
