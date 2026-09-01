/** Composer attachment validation and chip-state helpers (Phase 2). */

export const COMPOSER_ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024;
export const COMPOSER_ATTACHMENT_MAX_COUNT = 10;

export const COMPOSER_ATTACHMENT_EXTENSIONS = [
  ".txt",
  ".md",
  ".markdown",
  ".csv",
  ".json",
  ".xml",
  ".yaml",
  ".yml",
  ".html",
  ".htm",
  ".docx",
  ".xlsx",
  ".pdf",
  ".webm",
] as const;

export const COMPOSER_FILE_ACCEPT = COMPOSER_ATTACHMENT_EXTENSIONS.join(",");

/** Document uploads stay at 120s; audio transcription can approach the ~900s backend limit. */
export const COMPOSER_DOCUMENT_UPLOAD_TIMEOUT_MS = 120_000;
export const COMPOSER_AUDIO_UPLOAD_TIMEOUT_MS = 1_000_000;

const EXTENSION_SET = new Set<string>(COMPOSER_ATTACHMENT_EXTENSIONS);

export type ComposerAttachmentValidation =
  | { ok: true; extension: string }
  | { ok: false; message: string };

export type ComposerFileChip = {
  localId: string;
  name: string;
  state: "uploading" | "uploaded" | "error";
  attachmentId?: string;
  libraryItemId?: string;
  textExcerpt?: string | null;
  errorMessage?: string;
  deleting?: boolean;
};

export type AttachmentTranscriptionState = {
  status: "idle" | "transcribing" | "error";
  error?: string;
};

export function isUploadedWebmAttachment(file: ComposerFileChip): boolean {
  return (
    file.state === "uploaded" &&
    Boolean(file.attachmentId) &&
    composerAttachmentExtension(file.name) === ".webm"
  );
}

export function beginAttachmentTranscription(): AttachmentTranscriptionState {
  return { status: "transcribing" };
}

export function failAttachmentTranscription(error: string): AttachmentTranscriptionState {
  return { status: "error", error };
}

export async function applySuccessfulAttachmentTranscription(options: {
  transcript: string;
  insertTranscript: (transcript: string) => boolean;
  removeAttachment: () => Promise<void>;
}): Promise<boolean> {
  if (!options.insertTranscript(options.transcript)) return false;
  await options.removeAttachment();
  return true;
}

/** Uploaded pending chips with a backend ID need DELETE; local/error chips do not. */
export function shouldDeleteComposerAttachmentRemotely(file: ComposerFileChip): boolean {
  return file.state === "uploaded" && Boolean(file.attachmentId);
}

export function canStartComposerAttachmentDelete(file: ComposerFileChip): boolean {
  return shouldDeleteComposerAttachmentRemotely(file) && !file.deleting;
}

export function markComposerFileDeleting(
  files: ComposerFileChip[],
  localId: string,
  deleting: boolean,
): ComposerFileChip[] {
  return files.map((file) => (file.localId === localId ? { ...file, deleting } : file));
}

export function removeComposerFileByLocalId(
  files: ComposerFileChip[],
  localId: string,
): ComposerFileChip[] {
  return files.filter((file) => file.localId !== localId);
}

export function setComposerFileDeleteError(
  files: ComposerFileChip[],
  localId: string,
  errorMessage: string,
): ComposerFileChip[] {
  return files.map((file) =>
    file.localId === localId ? { ...file, deleting: false, errorMessage } : file,
  );
}

export function composerAttachmentExtension(filename: string): string {
  const base = filename.split(/[/\\]/).pop()?.trim() ?? "";
  const dot = base.lastIndexOf(".");
  if (dot <= 0 || dot === base.length - 1) return "";
  return base.slice(dot).toLowerCase();
}

export function composerAttachmentUploadTimeoutMs(filename: string): number {
  return composerAttachmentExtension(filename) === ".webm"
    ? COMPOSER_AUDIO_UPLOAD_TIMEOUT_MS
    : COMPOSER_DOCUMENT_UPLOAD_TIMEOUT_MS;
}

export function validateComposerAttachment(file: {
  name?: string | null;
  size?: number | null;
}): ComposerAttachmentValidation {
  const name = (file.name ?? "").trim();
  if (!name || name === "." || name === "..") {
    return { ok: false, message: "A valid filename is required" };
  }
  const size = typeof file.size === "number" ? file.size : 0;
  if (size <= 0) {
    return { ok: false, message: "Attachment is empty" };
  }
  if (size > COMPOSER_ATTACHMENT_MAX_BYTES) {
    return {
      ok: false,
      message: `File exceeds maximum size of ${COMPOSER_ATTACHMENT_MAX_BYTES / (1024 * 1024)} MB.`,
    };
  }
  const extension = composerAttachmentExtension(name);
  if (!EXTENSION_SET.has(extension)) {
    return {
      ok: false,
      message: "Unsupported file type. Upload a text file, .docx, .xlsx, .pdf, or .webm.",
    };
  }
  return { ok: true, extension };
}

export function hasUploadingComposerFiles(files: ComposerFileChip[]): boolean {
  return files.some((file) => file.state === "uploading");
}

/** Count chips that occupy a pending attachment slot (uploading or uploaded). */
export function countActiveComposerAttachments(files: ComposerFileChip[]): number {
  return files.filter((file) => file.state === "uploading" || file.state === "uploaded").length;
}

export function canAcceptMoreComposerAttachments(
  files: ComposerFileChip[],
  additional = 1,
  max = COMPOSER_ATTACHMENT_MAX_COUNT,
): boolean {
  return countActiveComposerAttachments(files) + additional <= max;
}

export function submittedAttachmentIds(files: ComposerFileChip[]): string[] {
  return files
    .filter((file) => file.state === "uploaded" && file.attachmentId)
    .map((file) => file.attachmentId as string);
}

export type PendingAttachmentItem = {
  id: string;
  filename: string;
  text_excerpt?: string | null;
  library_item_id?: string | null;
};

/**
 * Reconcile a pending-list GET into local chip state.
 * Additive only: never drops uploading/uploaded/error/deleting local chips just because
 * a (possibly stale) GET response omitted them.
 */
export function mergePendingAttachments(options: {
  current: ComposerFileChip[];
  serverPending: PendingAttachmentItem[];
}): ComposerFileChip[] {
  const { current, serverPending } = options;
  const knownAttachmentIds = new Set(
    current.map((file) => file.attachmentId).filter((id): id is string => Boolean(id)),
  );
  const knownLibraryIds = new Set(
    current.map((file) => file.libraryItemId).filter((id): id is string => Boolean(id)),
  );

  const additions: ComposerFileChip[] = [];
  const seenServerIds = new Set<string>();
  for (const item of serverPending) {
    if (!item.id || seenServerIds.has(item.id) || knownAttachmentIds.has(item.id)) continue;
    if (item.library_item_id && knownLibraryIds.has(item.library_item_id)) continue;
    seenServerIds.add(item.id);
    knownAttachmentIds.add(item.id);
    if (item.library_item_id) knownLibraryIds.add(item.library_item_id);
    additions.push({
      localId: item.id,
      name: item.filename,
      state: "uploaded",
      attachmentId: item.id,
      libraryItemId: item.library_item_id ?? undefined,
      textExcerpt: item.text_excerpt ?? null,
    });
  }

  // Preserve local order; append only missing server rows.
  return additions.length === 0 ? current : [...current, ...additions];
}

/** @deprecated Use mergePendingAttachments — kept as a thin alias for older call sites/tests. */
export function mergeRestoredComposerFiles(
  localFiles: ComposerFileChip[],
  pending: PendingAttachmentItem[],
): ComposerFileChip[] {
  return mergePendingAttachments({ current: localFiles, serverPending: pending });
}

/** Whether a pending-list response should be applied (chat + request generation still current). */
export function shouldApplyPendingAttachmentRestore(options: {
  requestedChatId: string;
  activeChatId: string | null;
  requestGeneration: number;
  latestGeneration: number;
}): boolean {
  if (!options.activeChatId || options.activeChatId !== options.requestedChatId) {
    return false;
  }
  return options.requestGeneration === options.latestGeneration;
}

export function apiErrorMessage(error: unknown, fallback = "Request failed"): string {
  if (error instanceof Error && error.message.trim()) {
    return error.message.trim();
  }
  if (typeof error === "string" && error.trim()) {
    return error.trim();
  }
  return fallback;
}

/**
 * Snapshot selected files then clear the input so the same path can be re-chosen.
 * Must copy before clearing: FileList is live and empties when value is reset.
 * Call this synchronously in the change handler before any await (e.g. createChat).
 */
export function captureComposerFileInputFiles(
  input: Pick<HTMLInputElement, "files" | "value">,
): File[] {
  const files = input.files ? Array.from(input.files) : [];
  input.value = "";
  return files;
}

/** Open the stable hidden file input from a direct user gesture; keep it mounted outside menus. */
export function openComposerFilePicker(input: HTMLInputElement | null): boolean {
  if (!input) return false;
  input.accept = COMPOSER_FILE_ACCEPT;
  input.click();
  return true;
}

/**
 * Menu → picker: open the native dialog first, then close the menu.
 * Closing first can drop the user-gesture chain or race the first change on some browsers.
 */
export function triggerComposerUploadFromMenu(
  input: HTMLInputElement | null,
  closeMenu: () => void,
): boolean {
  const opened = openComposerFilePicker(input);
  closeMenu();
  return opened;
}

/**
 * When activeChatId changes, composer chips are normally cleared.
 * Skip clearing for the null→newChat transition created by an in-flight first upload.
 */
export function shouldClearComposerFilesOnChatChange(options: {
  nextChatId: string | null;
  retainForChatId: string | null;
}): boolean {
  if (!options.nextChatId) return true;
  return options.retainForChatId !== options.nextChatId;
}

export function beginComposerUploadRetention(
  retainRef: { current: string | null },
  targetChatId: string,
): void {
  retainRef.current = targetChatId;
}

export function endComposerUploadRetention(
  retainRef: { current: string | null },
  targetChatId: string,
): void {
  if (retainRef.current === targetChatId) {
    retainRef.current = null;
  }
}

/**
 * Failed first-send cleanup must not delete a chat that an upload flow still owns
 * (retention / in-flight upload / already-persisted attachment chips).
 */
export function shouldSkipAutoDiscardUnusedChat(options: {
  chatId: string;
  retainForChatId: string | null;
  files: ComposerFileChip[];
}): boolean {
  if (options.retainForChatId === options.chatId) return true;
  if (hasUploadingComposerFiles(options.files)) return true;
  if (options.files.some((file) => file.state === "uploaded" && Boolean(file.attachmentId))) {
    return true;
  }
  return false;
}

export type ComposerUploadChip = ComposerFileChip;

export type RunComposerUploadsDeps = {
  activeChatId: string | null;
  createChat: (options?: {
    activate?: boolean;
    onChatCreated?: (chatId: string) => void;
  }) => Promise<string | null>;
  /** Required when createChat is called with activate:false (new-chat upload). */
  activateChat?: (chatId: string) => void;
  retainRef: { current: string | null };
  getActiveChatId: () => string | null;
  getFiles: () => ComposerUploadChip[];
  setFiles: (updater: (prev: ComposerUploadChip[]) => ComposerUploadChip[]) => void;
  uploadAttachment: (
    chatId: string,
    file: File,
  ) => Promise<{ id: string; text_excerpt?: string | null }>;
  onValidationError?: (fileName: string, message: string) => void;
  onUploadError?: (fileName: string, message: string) => void;
  onTooMany?: (message: string) => void;
};

/**
 * New-chat-safe upload orchestration:
 * 1) snapshot files (caller)
 * 2) create chat without activating (avoids chat-switch aborting this turn)
 * 3) queue chips + start attachment POSTs
 * 4) activate chat
 * 5) finish chip state updates
 */
export async function runComposerUploads(
  selectedFiles: File[],
  deps: RunComposerUploadsDeps,
): Promise<{ targetChatId: string | null; uploadAttempts: number }> {
  const files = selectedFiles.slice();
  if (!files.length) {
    return { targetChatId: null, uploadAttempts: 0 };
  }

  let targetChatId = deps.activeChatId;
  let createdNewChat = false;
  if (!targetChatId) {
    targetChatId = await deps.createChat({
      activate: false,
      onChatCreated: (chatId) => beginComposerUploadRetention(deps.retainRef, chatId),
    });
    if (!targetChatId) {
      return { targetChatId: null, uploadAttempts: 0 };
    }
    createdNewChat = true;
  } else {
    beginComposerUploadRetention(deps.retainRef, targetChatId);
  }

  let uploadAttempts = 0;
  const uploadJobs: Promise<void>[] = [];

  try {
    let activeSlots = countActiveComposerAttachments(deps.getFiles());
    for (const file of files) {
      const localId = `${Date.now()}-${file.name}-${Math.random().toString(36).slice(2, 8)}`;
      const displayName = file.name?.trim() || "upload";
      if (activeSlots >= COMPOSER_ATTACHMENT_MAX_COUNT) {
        deps.onTooMany?.(
          `A chat can have at most ${COMPOSER_ATTACHMENT_MAX_COUNT} pending attachments.`,
        );
        break;
      }
      const validation = validateComposerAttachment(file);
      if (!validation.ok) {
        deps.setFiles((prev) => [
          ...prev,
          {
            localId,
            name: displayName,
            state: "error",
            errorMessage: validation.message,
          },
        ]);
        deps.onValidationError?.(displayName, validation.message);
        continue;
      }

      activeSlots += 1;
      deps.setFiles((prev) => [...prev, { localId, name: displayName, state: "uploading" }]);

      uploadAttempts += 1;
      const chatIdForUpload = targetChatId;
      // Start the POST before activating the new chat so chat-switch effects cannot
      // interrupt control flow before uploadAttachment is invoked.
      uploadJobs.push(
        (async () => {
          try {
            const uploaded = await deps.uploadAttachment(chatIdForUpload, file);
            const active = deps.getActiveChatId();
            if (active !== null && active !== chatIdForUpload) return;
            if (active === null && deps.retainRef.current !== chatIdForUpload) return;
            deps.setFiles((prev) =>
              prev.map((item) =>
                item.localId === localId
                  ? {
                      ...item,
                      state: "uploaded" as const,
                      attachmentId: uploaded.id,
                      textExcerpt: uploaded.text_excerpt,
                      errorMessage: undefined,
                    }
                  : item,
              ),
            );
          } catch (error) {
            activeSlots -= 1;
            const message = apiErrorMessage(error, "Upload failed");
            const active = deps.getActiveChatId();
            if (active !== null && active !== chatIdForUpload) return;
            if (active === null && deps.retainRef.current !== chatIdForUpload) return;
            deps.setFiles((prev) =>
              prev.map((item) =>
                item.localId === localId
                  ? { ...item, state: "error" as const, errorMessage: message }
                  : item,
              ),
            );
            deps.onUploadError?.(displayName, message);
          }
        })(),
      );
    }

    if (createdNewChat) {
      deps.activateChat?.(targetChatId);
    }

    await Promise.all(uploadJobs);
  } finally {
    endComposerUploadRetention(deps.retainRef, targetChatId);
  }

  return { targetChatId, uploadAttempts };
}
