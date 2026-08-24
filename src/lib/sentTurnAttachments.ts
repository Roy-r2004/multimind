import type { ApiTurnAttachment } from "./api/types.ts";

export type SentTurnAttachmentItem = ApiTurnAttachment & {
  typeLabel: string;
};

export function attachmentTypeLabel(filename: string): string {
  const extension = filename.split(".").pop()?.trim();
  return extension && extension !== filename ? extension.toUpperCase() : "FILE";
}

export function sentTurnAttachmentItems(
  attachments: ApiTurnAttachment[] | undefined,
): SentTurnAttachmentItem[] {
  return (attachments ?? []).map((attachment) => ({
    ...attachment,
    typeLabel: attachmentTypeLabel(attachment.filename),
  }));
}
