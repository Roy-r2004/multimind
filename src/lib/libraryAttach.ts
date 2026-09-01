/** Helpers for attaching Library items to the chat composer. */

import type { ApiChatAttachment } from "./api/types.ts";
import type { ComposerFileChip } from "./composerAttachments.ts";
import {
  beginComposerUploadRetention,
  COMPOSER_ATTACHMENT_MAX_COUNT,
  countActiveComposerAttachments,
  endComposerUploadRetention,
} from "./composerAttachments.ts";

export type AttachLibraryItemDeps = {
  activeChatId: string | null;
  createChat: (options?: {
    activate?: boolean;
    onChatCreated?: (chatId: string) => void;
  }) => Promise<string | null>;
  activateChat: (chatId: string) => void;
  retainRef: { current: string | null };
  getFiles: () => ComposerFileChip[];
  setFiles: (updater: (prev: ComposerFileChip[]) => ComposerFileChip[]) => void;
  attachFromLibrary: (chatId: string, libraryItemId: string) => Promise<ApiChatAttachment>;
  onTooMany?: (message: string) => void;
  onError?: (message: string) => void;
};

/** Gate an attach behind persistence when the editor has unsaved content. */
export async function saveLibraryDocumentBeforeAttach(
  dirty: boolean,
  save: () => Promise<boolean>,
): Promise<boolean> {
  return dirty ? save() : true;
}

/**
 * Attach a Library item to the current chat composer.
 * Creates a chat (with retention) when none is active. Does not send a turn.
 */
export async function attachLibraryItemToComposer(
  libraryItemId: string,
  displayName: string,
  deps: AttachLibraryItemDeps,
): Promise<{
  chatId: string | null;
  attachment: ApiChatAttachment | null;
  createdNewChat: boolean;
}> {
  const existing = deps
    .getFiles()
    .find(
      (file) =>
        file.state === "uploaded" && file.attachmentId && file.libraryItemId === libraryItemId,
    );
  if (existing) {
    return {
      chatId: deps.activeChatId,
      attachment: existing.attachmentId
        ? {
            id: existing.attachmentId,
            filename: existing.name,
            content_type: null,
            size_bytes: 0,
            text_excerpt: existing.textExcerpt ?? null,
            excerpt_status: "ready",
            library_item_id: libraryItemId,
          }
        : null,
      createdNewChat: false,
    };
  }

  if (countActiveComposerAttachments(deps.getFiles()) >= COMPOSER_ATTACHMENT_MAX_COUNT) {
    deps.onTooMany?.(
      `A chat can have at most ${COMPOSER_ATTACHMENT_MAX_COUNT} pending attachments.`,
    );
    return { chatId: deps.activeChatId, attachment: null, createdNewChat: false };
  }

  let targetChatId = deps.activeChatId;
  let createdNewChat = false;
  if (!targetChatId) {
    targetChatId = await deps.createChat({
      activate: false,
      onChatCreated: (chatId) => beginComposerUploadRetention(deps.retainRef, chatId),
    });
    if (!targetChatId) {
      deps.onError?.("Could not create a chat for this attachment");
      return { chatId: null, attachment: null, createdNewChat: false };
    }
    createdNewChat = true;
  } else {
    beginComposerUploadRetention(deps.retainRef, targetChatId);
  }

  const localId = `library-${libraryItemId}-${Date.now()}`;
  deps.setFiles((prev) => [
    ...prev,
    {
      localId,
      name: displayName,
      state: "uploading",
      libraryItemId,
    },
  ]);

  try {
    const attachment = await deps.attachFromLibrary(targetChatId, libraryItemId);
    deps.setFiles((prev) =>
      prev.map((file) =>
        file.localId === localId
          ? {
              ...file,
              state: "uploaded" as const,
              attachmentId: attachment.id,
              textExcerpt: attachment.text_excerpt,
              libraryItemId,
            }
          : file,
      ),
    );

    if (createdNewChat) {
      // Activate after the attach POST so chat-switch clear cannot drop the chip.
      deps.activateChat(targetChatId);
    }
    return { chatId: targetChatId, attachment, createdNewChat };
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to attach Library item";
    deps.setFiles((prev) =>
      prev.map((file) =>
        file.localId === localId
          ? { ...file, state: "error" as const, errorMessage: message }
          : file,
      ),
    );
    deps.onError?.(message);
    return { chatId: targetChatId, attachment: null, createdNewChat };
  } finally {
    endComposerUploadRetention(deps.retainRef, targetChatId);
  }
}

/**
 * Pure helper: decide whether a pending library attach should be skipped as a duplicate.
 */
export function findDuplicateLibraryAttachment(
  files: ComposerFileChip[],
  libraryItemId: string,
): ComposerFileChip | undefined {
  return files.find(
    (file) =>
      (file.state === "uploading" || file.state === "uploaded") &&
      file.libraryItemId === libraryItemId,
  );
}

/**
 * When attaching from a non-chat page, composer local state is unmounted.
 * Persist via API first, then navigate; chat remount merges pending attachments.
 */
export async function attachLibraryItemViaApi(options: {
  libraryItemId: string;
  activeChatId: string | null;
  createChat: (options?: {
    activate?: boolean;
    onChatCreated?: (chatId: string) => void;
  }) => Promise<string | null>;
  activateChat: (chatId: string) => void;
  retainRef: { current: string | null };
  attachFromLibrary: (chatId: string, libraryItemId: string) => Promise<ApiChatAttachment>;
}): Promise<{
  chatId: string | null;
  attachment: ApiChatAttachment | null;
  createdNewChat: boolean;
}> {
  let targetChatId = options.activeChatId;
  let createdNewChat = false;
  if (!targetChatId) {
    targetChatId = await options.createChat({
      activate: false,
      onChatCreated: (chatId) => beginComposerUploadRetention(options.retainRef, chatId),
    });
    if (!targetChatId) {
      return { chatId: null, attachment: null, createdNewChat: false };
    }
    createdNewChat = true;
  } else {
    beginComposerUploadRetention(options.retainRef, targetChatId);
  }

  try {
    const attachment = await options.attachFromLibrary(targetChatId, options.libraryItemId);
    if (createdNewChat) {
      options.activateChat(targetChatId);
    }
    return { chatId: targetChatId, attachment, createdNewChat };
  } finally {
    endComposerUploadRetention(options.retainRef, targetChatId);
  }
}
