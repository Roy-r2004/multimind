import { useEffect, useRef, useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  Download,
  ExternalLink,
  Loader2,
  Maximize2,
  Minimize2,
  Paperclip,
  RefreshCw,
  X,
} from "lucide-react";
import { MessageContent } from "@/components/chat/MessageContent";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import type { ApiLibraryItem } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";
import { libraryFileContentView } from "@/lib/libraryContent";
import { formatLibraryBytes, formatLibraryUpdatedAt, libraryItemTypeLabel } from "@/lib/libraryUi";
import { cn } from "@/lib/utils";

/**
 * Read-only quick preview for a Library item. Fetches the document/file
 * content detail (the list endpoint never includes it) and reuses the same
 * renderers as the full editor/detail page so Markdown tables and file
 * fallbacks look identical everywhere.
 */
export function LibraryPreviewPanel({
  summary,
  onClose,
  onOpenFull,
  onDownload,
  onAttach,
  attaching,
  onNavigate,
  hasPrev,
  hasNext,
  className,
  showCloseButton = true,
  expanded,
  onToggleExpand,
}: {
  summary: ApiLibraryItem;
  onClose: () => void;
  onOpenFull: () => void;
  onDownload: () => void;
  onAttach: () => void;
  attaching: boolean;
  onNavigate: (direction: "prev" | "next") => void;
  hasPrev: boolean;
  hasNext: boolean;
  className?: string;
  /** False when a host (e.g. a Sheet) already provides its own close affordance. */
  showCloseButton?: boolean;
  /** Omit entirely on hosts that are already full-width (e.g. the mobile Sheet). */
  expanded?: boolean;
  onToggleExpand?: () => void;
}) {
  const { authHeaders } = useAuth();
  const [detail, setDetail] = useState<ApiLibraryItem | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Bumped on every fetch so a response for a since-superseded item (the user
  // clicked through to another document before this one resolved) is dropped
  // instead of flashing stale content under the new header.
  const generationRef = useRef(0);

  function fetchDetail() {
    const auth = authHeaders();
    if (!auth) {
      setError("Sign in to preview this item.");
      setLoading(false);
      return;
    }
    const generation = ++generationRef.current;
    setLoading(true);
    setError(null);
    api.library.getItem(auth, summary.id).then(
      (loaded) => {
        if (generationRef.current !== generation) return;
        setDetail(loaded);
        setLoading(false);
      },
      (fetchError) => {
        if (generationRef.current !== generation) return;
        setError(fetchError instanceof Error ? fetchError.message : "Failed to load preview");
        setLoading(false);
      },
    );
  }

  useEffect(() => {
    fetchDetail();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- fetchDetail reads summary.id freshly each call; only the id itself should retrigger the fetch.
  }, [authHeaders, summary.id]);

  // Only trust `detail` once it actually matches the item we're currently
  // showing a header for — otherwise treat it as not-yet-loaded.
  const activeDetail = detail?.id === summary.id ? detail : null;
  const isDocument = summary.item_type === "document";
  const isFile = summary.item_type === "file";
  const typeLabel = libraryItemTypeLabel(summary);
  const metaLine = [
    typeLabel,
    summary.size_bytes != null ? formatLibraryBytes(summary.size_bytes) : null,
    summary.updated_at ? `Updated ${formatLibraryUpdatedAt(summary.updated_at)}` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div
      className={cn("flex h-full min-w-0 flex-col", className)}
      data-testid="library-preview-panel"
    >
      <div className="flex shrink-0 items-start justify-between gap-2 border-b border-border px-4 py-3">
        <div className="min-w-0">
          <p className="truncate font-display text-base font-semibold" title={summary.title}>
            {summary.title}
          </p>
          <p className="mt-0.5 truncate text-xs text-muted-foreground">{metaLine}</p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Button
            type="button"
            variant="ghost"
            size="icon"
            disabled={!hasPrev}
            onClick={() => onNavigate("prev")}
            title="Previous item"
            aria-label="Preview previous item"
          >
            <ChevronLeft className="size-4" />
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            disabled={!hasNext}
            onClick={() => onNavigate("next")}
            title="Next item"
            aria-label="Preview next item"
          >
            <ChevronRight className="size-4" />
          </Button>
          {showCloseButton && (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              onClick={onClose}
              title="Close preview"
              aria-label="Close preview"
            >
              <X className="size-4" />
            </Button>
          )}
        </div>
      </div>

      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border px-4 py-2">
        <Button type="button" variant="outline" size="sm" onClick={onOpenFull}>
          <ExternalLink className="size-3.5" />
          {isDocument ? "Open full document" : "Open"}
        </Button>
        <Button type="button" variant="outline" size="sm" disabled={attaching} onClick={onAttach}>
          {attaching ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <Paperclip className="size-3.5" />
          )}
          Attach to Chat
        </Button>
        {isFile && (
          <Button type="button" variant="outline" size="sm" onClick={onDownload}>
            <Download className="size-3.5" />
            Download
          </Button>
        )}
        {onToggleExpand && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={onToggleExpand}
            className="ml-auto"
          >
            {expanded ? <Minimize2 className="size-3.5" /> : <Maximize2 className="size-3.5" />}
            {expanded ? "Collapse" : "Expand"}
          </Button>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        {error ? (
          <div className="flex flex-col items-start gap-2 text-sm text-muted-foreground">
            <p>{error}</p>
            <Button type="button" variant="outline" size="sm" onClick={fetchDetail}>
              <RefreshCw className="size-3.5" />
              Try again
            </Button>
          </div>
        ) : !activeDetail || loading ? (
          <div className="space-y-2">
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-5/6" />
            <Skeleton className="h-4 w-2/3" />
          </div>
        ) : isDocument ? (
          <PreviewDocumentBody content={activeDetail.content_text ?? ""} wide={Boolean(expanded)} />
        ) : (
          <PreviewFileBody item={activeDetail} wide={Boolean(expanded)} />
        )}
      </div>
    </div>
  );
}

function PreviewDocumentBody({ content, wide }: { content: string; wide: boolean }) {
  if (!content.trim()) {
    return <p className="text-sm text-muted-foreground">This document is empty.</p>;
  }
  return (
    <div
      data-testid="library-preview-document-content"
      className={cn(
        "min-w-0 font-sans text-sm leading-[1.7]",
        wide ? "max-w-[64rem]" : "max-w-[48rem]",
      )}
    >
      <MessageContent variant="verdict" showTableCopy={false}>
        {content}
      </MessageContent>
    </div>
  );
}

function PreviewFileBody({ item, wide }: { item: ApiLibraryItem; wide: boolean }) {
  const view = libraryFileContentView(item);
  if (view.kind === "text") {
    return (
      <pre
        className={cn(
          "whitespace-pre-wrap break-words font-mono text-sm leading-relaxed text-foreground",
          wide ? "max-w-[64rem]" : "max-w-[48rem]",
        )}
      >
        {view.text}
      </pre>
    );
  }
  return (
    <div className="rounded-lg border border-dashed border-border bg-muted/30 px-4 py-8 text-center text-sm text-muted-foreground">
      {view.message}
    </div>
  );
}
