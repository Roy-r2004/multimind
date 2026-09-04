import {
  useLayoutEffect,
  useRef,
  useState,
  type ClipboardEvent,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import {
  Bold,
  Heading2,
  IndentDecrease,
  IndentIncrease,
  Italic,
  List,
  ListChecks,
  ListOrdered,
} from "lucide-react";
import { MessageContent } from "@/components/chat/MessageContent";
import { Button } from "@/components/ui/button";
import {
  formatLibraryDocumentBlock,
  formatLibraryDocumentInline,
  formatLibraryDocumentList,
  handleLibraryDocumentListKey,
  type LibraryListKind,
  type TextareaEdit,
} from "@/lib/libraryDocumentFormatting";
import { applyLibraryTablePaste } from "@/lib/libraryHtmlTable";
import {
  LIBRARY_DOCUMENT_DEFAULT_MODE,
  libraryDocumentPreviewContent,
  type LibraryDocumentMode,
} from "@/lib/libraryDocumentPreview";
import { cn } from "@/lib/utils";

type Props = {
  id?: string;
  value: string;
  onChange: (value: string) => void;
  rows?: number;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
};

export function LibraryDocumentEditor({
  id,
  value,
  onChange,
  rows = 10,
  placeholder,
  disabled,
  className,
}: Props) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const pendingSelection = useRef<Pick<TextareaEdit, "selectionStart" | "selectionEnd"> | null>(
    null,
  );
  const [mode, setMode] = useState<LibraryDocumentMode>(LIBRARY_DOCUMENT_DEFAULT_MODE);

  useLayoutEffect(() => {
    const selection = pendingSelection.current;
    const textarea = textareaRef.current;
    if (!selection || !textarea) return;
    pendingSelection.current = null;
    textarea.focus();
    textarea.setSelectionRange(selection.selectionStart, selection.selectionEnd);
  }, [value]);

  function applyEdit(edit: TextareaEdit) {
    if (edit.text === value) return;
    pendingSelection.current = edit;
    onChange(edit.text);
  }

  function toggleList(kind: LibraryListKind) {
    const textarea = textareaRef.current;
    if (!textarea) return;
    applyEdit(
      formatLibraryDocumentList(value, textarea.selectionStart, textarea.selectionEnd, kind),
    );
  }

  function applyInline(kind: "bold" | "italic") {
    const textarea = textareaRef.current;
    if (!textarea) return;
    applyEdit(
      formatLibraryDocumentInline(value, textarea.selectionStart, textarea.selectionEnd, kind),
    );
  }

  function applyBlock(kind: "heading" | "checklist" | "indent" | "outdent") {
    const textarea = textareaRef.current;
    if (!textarea) return;
    applyEdit(
      formatLibraryDocumentBlock(value, textarea.selectionStart, textarea.selectionEnd, kind),
    );
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (
      (event.ctrlKey || event.metaKey) &&
      !event.altKey &&
      (event.key.toLowerCase() === "b" || event.key.toLowerCase() === "i")
    ) {
      event.preventDefault();
      applyInline(event.key.toLowerCase() === "b" ? "bold" : "italic");
      return;
    }
    if (event.key !== "Enter" && event.key !== "Backspace") return;
    const edit = handleLibraryDocumentListKey(
      value,
      event.currentTarget.selectionStart,
      event.currentTarget.selectionEnd,
      event.key,
    );
    if (!edit) return;
    event.preventDefault();
    applyEdit(edit);
  }

  function onPaste(event: ClipboardEvent<HTMLTextAreaElement>) {
    if (disabled) return;
    const clipboard = event.clipboardData;
    if (!clipboard) return;
    const edit = applyLibraryTablePaste(
      value,
      event.currentTarget.selectionStart,
      event.currentTarget.selectionEnd,
      clipboard.getData("text/html"),
      clipboard.getData("text/plain"),
    );
    if (!edit) return;
    event.preventDefault();
    applyEdit(edit);
  }

  const previewText = libraryDocumentPreviewContent(value);

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-1 rounded-lg border border-border bg-muted/30 p-1">
        {mode === "edit" ? (
          <>
            <ToolbarButton label="Bold" disabled={disabled} onClick={() => applyInline("bold")}>
              <Bold className="size-4" />
            </ToolbarButton>
            <ToolbarButton label="Italic" disabled={disabled} onClick={() => applyInline("italic")}>
              <Italic className="size-4" />
            </ToolbarButton>
            <ToolbarButton label="Heading" disabled={disabled} onClick={() => applyBlock("heading")}>
              <Heading2 className="size-4" />
            </ToolbarButton>
            <div className="mx-0.5 h-5 w-px bg-border" aria-hidden="true" />
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={disabled}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => toggleList("bullet")}
              aria-label="Bulleted list"
              title="Bulleted list"
            >
              <List className="size-4" />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={disabled}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => toggleList("numbered")}
              aria-label="Numbered list"
              title="Numbered list"
            >
              <ListOrdered className="size-4" />
            </Button>
            <ToolbarButton
              label="Checklist"
              disabled={disabled}
              onClick={() => applyBlock("checklist")}
            >
              <ListChecks className="size-4" />
            </ToolbarButton>
            <div className="mx-0.5 h-5 w-px bg-border" aria-hidden="true" />
            <ToolbarButton label="Outdent" disabled={disabled} onClick={() => applyBlock("outdent")}>
              <IndentDecrease className="size-4" />
            </ToolbarButton>
            <ToolbarButton label="Indent" disabled={disabled} onClick={() => applyBlock("indent")}>
              <IndentIncrease className="size-4" />
            </ToolbarButton>
          </>
        ) : (
          <span className="px-2 text-xs text-muted-foreground">Preview</span>
        )}
        <div
          className="ml-auto flex items-center rounded-md border border-border bg-background/80 p-0.5"
          role="tablist"
          aria-label="Document view"
        >
          <Button
            type="button"
            variant={mode === "edit" ? "secondary" : "ghost"}
            size="sm"
            aria-pressed={mode === "edit"}
            onClick={() => setMode("edit")}
          >
            Edit
          </Button>
          <Button
            type="button"
            variant={mode === "preview" ? "secondary" : "ghost"}
            size="sm"
            aria-pressed={mode === "preview"}
            onClick={() => setMode("preview")}
          >
            Preview
          </Button>
        </div>
      </div>
      {mode === "edit" ? (
        <textarea
          ref={textareaRef}
          id={id}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={onKeyDown}
          onPaste={onPaste}
          rows={rows}
          placeholder={placeholder}
          disabled={disabled}
          className={cn(
            "w-full resize-y rounded-lg border border-border bg-background/60 px-3 py-3 font-sans text-sm leading-[1.7] outline-none focus:ring-2 focus:ring-ring",
            className,
          )}
        />
      ) : (
        <div
          data-testid="library-document-preview"
          className={cn(
            "min-w-0 overflow-x-auto rounded-lg border border-border bg-background/60 px-3 py-3 font-sans text-sm leading-[1.7]",
            className,
          )}
        >
          {previewText.trim() ? (
            <MessageContent>{previewText}</MessageContent>
          ) : (
            <p className="text-sm text-muted-foreground">Nothing to preview yet.</p>
          )}
        </div>
      )}
    </div>
  );
}

function ToolbarButton({
  label,
  disabled,
  onClick,
  children,
}: {
  label: string;
  disabled?: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      disabled={disabled}
      onMouseDown={(event) => event.preventDefault()}
      onClick={onClick}
      aria-label={label}
      title={label}
    >
      {children}
    </Button>
  );
}
