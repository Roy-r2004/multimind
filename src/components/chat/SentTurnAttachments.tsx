import { FileText } from "lucide-react";
import type { ApiTurnAttachment } from "../../lib/api/types";
import { sentTurnAttachmentItems } from "../../lib/sentTurnAttachments";

type Props = {
  attachments?: ApiTurnAttachment[];
};

export function SentTurnAttachments({ attachments = [] }: Props) {
  const items = sentTurnAttachmentItems(attachments);
  if (items.length === 0) return null;

  return (
    <div className="mt-3 flex flex-col items-end gap-1.5" aria-label="Attached files">
      {items.map((attachment) => (
        <div
          key={attachment.id}
          title={attachment.filename}
          className="flex max-w-full items-center gap-2 rounded-lg border border-primary-foreground/20 bg-primary-foreground/10 px-2.5 py-1.5"
        >
          <FileText className="size-3.5 shrink-0 opacity-80" aria-hidden="true" />
          <span className="shrink-0 text-[10px] font-bold tracking-wide opacity-75">
            {attachment.typeLabel}
          </span>
          <span className="min-w-0 truncate text-xs font-medium">{attachment.filename}</span>
        </div>
      ))}
    </div>
  );
}
