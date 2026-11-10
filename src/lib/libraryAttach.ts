/** Helpers for attaching Library items to the chat composer. */

import type { ApiChatAttachment } from "./api/types.ts";
import type { ComposerFileChip } from "./composerAttachments.ts";
import {
  beginComposerUploadRetention,
  COMPOSER_ATTACHMENT_MAX_COUNT,
  countActiveComposerAttachments,
  endComposerUploadRetention,
} from "./composerAttachments.ts";
import { mapWithConcurrencyLimit, LIBRARY_BULK_CONCURRENCY_LIMIT } from "./libraryBulkActions.ts";

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
 * After `saveLibraryDocumentBeforeAttach` resolves, re-check dirty against
 * its live value (not the one captured before the save started): if the
 * user kept typing while that save was in flight, the just-persisted
 * snapshot is already stale. Attaching it — or navigating away — would
 * silently leave those newer edits unsaved, so the caller must abort.
 */
export function shouldAbortAttachForStaleEdits(dirtyAfterSave: boolean): boolean {
  return dirtyAfterSave;
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
              excerptStatus: attachment.excerpt_status,
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

export type AttachLibraryItemsResult = {
  chatId: string | null;
  createdNewChat: boolean;
  successIds: string[];
  failedIds: string[];
};

export type LibraryBulkAttachLimitCheck =
  | { allowed: true; newCount: number }
  | { allowed: false; newCount: number; message: string };

/**
 * Pure limit math: a reattach of an item already pending on the chat
 * refreshes that row instead of consuming a new slot, so only the
 * genuinely-new ids count against the chat's existing pending total.
 */
export function computeLibraryBulkAttachLimit(params: {
  selectedIds: string[];
  existingPendingLibraryItemIds: Iterable<string>;
  existingPendingCount: number;
  maxPendingAttachments?: number;
}): LibraryBulkAttachLimitCheck {
  const max = params.maxPendingAttachments ?? COMPOSER_ATTACHMENT_MAX_COUNT;
  const existing = new Set(params.existingPendingLibraryItemIds);
  const newCount = params.selectedIds.filter((id) => !existing.has(id)).length;
  const projectedTotal = params.existingPendingCount + newCount;
  if (projectedTotal <= max) {
    return { allowed: true, newCount };
  }
  const message =
    params.existingPendingCount > 0
      ? `This chat already has ${params.existingPendingCount} pending attachment` +
        `${params.existingPendingCount === 1 ? "" : "s"}; attaching ${newCount} more ` +
        `would exceed the limit of ${max}.`
      : `You can attach at most ${max} items to a chat at once.`;
  return { allowed: false, newCount, message };
}

/**
 * Bulk counterpart to `attachLibraryItemViaApi`: attaches every id to a
 * single, once-resolved chat (never one chat per item), reusing the exact
 * same per-item `attachFromLibrary` call. Rejects up front — before any
 * attach call — when the selection would push the target chat's pending
 * attachments over the limit, accounting for both attachments the chat
 * already has and any selected items already pending on it (which refresh
 * in place rather than consuming a new slot). Per-item failures are
 * reported individually rather than aborting the whole batch.
 */
export async function attachLibraryItemsViaApi(options: {
  libraryItemIds: string[];
  activeChatId: string | null;
  createChat: (options?: {
    activate?: boolean;
    onChatCreated?: (chatId: string) => void;
  }) => Promise<string | null>;
  activateChat: (chatId: string) => void;
  retainRef: { current: string | null };
  attachFromLibrary: (chatId: string, libraryItemId: string) => Promise<ApiChatAttachment>;
  /** Pending attachments (library-based and uploaded files alike) already on a known chat. */
  listPendingAttachments: (chatId: string) => Promise<ApiChatAttachment[]>;
  concurrencyLimit?: number;
  maxPendingAttachments?: number;
  onTooMany?: (message: string) => void;
  onError?: (message: string) => void;
}): Promise<AttachLibraryItemsResult> {
  const ids = options.libraryItemIds;
  if (ids.length === 0) {
    return { chatId: options.activeChatId, createdNewChat: false, successIds: [], failedIds: [] };
  }

  let targetChatId = options.activeChatId;
  let createdNewChat = false;

  if (targetChatId) {
    let existingPending: ApiChatAttachment[];
    try {
      existingPending = await options.listPendingAttachments(targetChatId);
    } catch (error) {
      options.onError?.(
        error instanceof Error ? error.message : "Could not check this chat's existing attachments",
      );
      return { chatId: targetChatId, createdNewChat: false, successIds: [], failedIds: [...ids] };
    }
    const existingLibraryItemIds = existingPending
      .map((attachment) => attachment.library_item_id)
      .filter((id): id is string => Boolean(id));
    const check = computeLibraryBulkAttachLimit({
      selectedIds: ids,
      existingPendingLibraryItemIds: existingLibraryItemIds,
      existingPendingCount: existingPending.length,
      maxPendingAttachments: options.maxPendingAttachments,
    });
    if (!check.allowed) {
      options.onTooMany?.(check.message);
      return { chatId: targetChatId, createdNewChat: false, successIds: [], failedIds: [...ids] };
    }
    beginComposerUploadRetention(options.retainRef, targetChatId);
  } else {
    // No chat yet, so it starts with zero pending attachments — the only
    // possible breach is the selection itself.
    const check = computeLibraryBulkAttachLimit({
      selectedIds: ids,
      existingPendingLibraryItemIds: [],
      existingPendingCount: 0,
      maxPendingAttachments: options.maxPendingAttachments,
    });
    if (!check.allowed) {
      options.onTooMany?.(check.message);
      return { chatId: null, createdNewChat: false, successIds: [], failedIds: [...ids] };
    }
    targetChatId = await options.createChat({
      activate: false,
      onChatCreated: (chatId) => beginComposerUploadRetention(options.retainRef, chatId),
    });
    if (!targetChatId) {
      options.onError?.("Could not create a chat for these attachments");
      return { chatId: null, createdNewChat: false, successIds: [], failedIds: [...ids] };
    }
    createdNewChat = true;
  }

  try {
    const settled = await mapWithConcurrencyLimit(
      ids,
      options.concurrencyLimit ?? LIBRARY_BULK_CONCURRENCY_LIMIT,
      (libraryItemId) => options.attachFromLibrary(targetChatId as string, libraryItemId),
    );
    const successIds: string[] = [];
    const failedIds: string[] = [];
    ids.forEach((id, index) => {
      if (settled[index]?.status === "fulfilled") successIds.push(id);
      else failedIds.push(id);
    });
    // Activate only after the attach calls land — same ordering reason as
    // the single-item path: a chat-switch clear can't drop chips that way.
    if (successIds.length > 0 && createdNewChat) {
      options.activateChat(targetChatId);
    }
    return { chatId: targetChatId, createdNewChat, successIds, failedIds };
  } finally {
    endComposerUploadRetention(options.retainRef, targetChatId);
  }
}
