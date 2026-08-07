import assert from "node:assert/strict";
import test from "node:test";

import { resolveFailedResponseMessage } from "../../src/lib/api/errorMessage.ts";
import {
  COMPOSER_ATTACHMENT_MAX_BYTES,
  COMPOSER_FILE_ACCEPT,
  apiErrorMessage,
  canAcceptMoreComposerAttachments,
  canStartComposerAttachmentDelete,
  captureComposerFileInputFiles,
  countActiveComposerAttachments,
  hasUploadingComposerFiles,
  markComposerFileDeleting,
  mergePendingAttachments,
  mergeRestoredComposerFiles,
  openComposerFilePicker,
  removeComposerFileByLocalId,
  removeSubmittedComposerFiles,
  runComposerUploads,
  setComposerFileDeleteError,
  shouldApplyPendingAttachmentRestore,
  shouldClearComposerFilesOnChatChange,
  shouldDeleteComposerAttachmentRemotely,
  shouldSkipAutoDiscardUnusedChat,
  submittedAttachmentIds,
  triggerComposerUploadFromMenu,
  validateComposerAttachment,
  type ComposerFileChip,
} from "../../src/lib/composerAttachments.ts";

test("accept list includes docx xlsx and pdf and excludes images", () => {
  assert.match(COMPOSER_FILE_ACCEPT, /\.docx/);
  assert.match(COMPOSER_FILE_ACCEPT, /\.xlsx/);
  assert.match(COMPOSER_FILE_ACCEPT, /\.pdf/);
  assert.equal(COMPOSER_FILE_ACCEPT.includes("image"), false);
  assert.equal(COMPOSER_FILE_ACCEPT.includes(".png"), false);
});

test("validates supported extensions including docx xlsx and pdf", () => {
  assert.equal(validateComposerAttachment({ name: "a.docx", size: 12 }).ok, true);
  assert.equal(validateComposerAttachment({ name: "b.xlsx", size: 12 }).ok, true);
  assert.equal(validateComposerAttachment({ name: "c.pdf", size: 12 }).ok, true);
  assert.equal(validateComposerAttachment({ name: "c.txt", size: 12 }).ok, true);
});

test("rejects empty, oversized, and unsupported files before network", () => {
  assert.equal(validateComposerAttachment({ name: "a.txt", size: 0 }).ok, false);
  assert.equal(
    validateComposerAttachment({ name: "a.txt", size: COMPOSER_ATTACHMENT_MAX_BYTES + 1 }).ok,
    false,
  );
  const bad = validateComposerAttachment({ name: "photo.png", size: 10 });
  assert.equal(bad.ok, false);
  if (!bad.ok) {
    assert.match(bad.message, /Unsupported/i);
  }
});

test("rejects missing filename", () => {
  const result = validateComposerAttachment({ name: "   ", size: 4 });
  assert.equal(result.ok, false);
});

test("send helpers detect uploading and collect submitted ids", () => {
  const files = [
    { localId: "1", name: "a.txt", state: "uploading" as const },
    { localId: "2", name: "b.txt", state: "uploaded" as const, attachmentId: "att-b" },
    { localId: "3", name: "c.txt", state: "error" as const, errorMessage: "boom" },
  ];
  assert.equal(hasUploadingComposerFiles(files), true);
  assert.deepEqual(submittedAttachmentIds(files), ["att-b"]);
});

test("keyboard send is blocked conceptually when uploading", () => {
  const files = [{ localId: "1", name: "a.txt", state: "uploading" as const }];
  assert.equal(hasUploadingComposerFiles(files), true);
});

test("successful send removes only submitted uploaded ids", () => {
  const files = [
    { localId: "1", name: "a.txt", state: "uploaded" as const, attachmentId: "att-a" },
    { localId: "2", name: "b.txt", state: "uploaded" as const, attachmentId: "att-b" },
    { localId: "3", name: "c.txt", state: "error" as const, errorMessage: "nope" },
    { localId: "4", name: "d.txt", state: "uploading" as const },
    { localId: "5", name: "e.txt", state: "uploaded" as const, attachmentId: "att-new" },
  ];
  const next = removeSubmittedComposerFiles(files, ["att-a", "att-b"]);
  assert.deepEqual(
    next.map((f) => f.localId),
    ["3", "4", "5"],
  );
});

test("failed chips remain after send cleanup", () => {
  const files = [
    { localId: "1", name: "ok.txt", state: "uploaded" as const, attachmentId: "att-1" },
    { localId: "2", name: "bad.txt", state: "error" as const, errorMessage: "Failed" },
  ];
  const next = removeSubmittedComposerFiles(files, ["att-1"]);
  assert.equal(next.length, 1);
  assert.equal(next[0]?.state, "error");
  assert.equal(next[0]?.errorMessage, "Failed");
});

test("pending restore does not duplicate and keeps local busy chips", () => {
  const local = [
    { localId: "up-1", name: "new.txt", state: "uploading" as const },
    {
      localId: "err-1",
      name: "bad.png",
      state: "error" as const,
      errorMessage: "Unsupported",
    },
  ];
  const pending = [
    { id: "att-1", filename: "saved.txt", text_excerpt: "hello" },
    { id: "att-1", filename: "saved.txt", text_excerpt: "hello" },
  ];
  const merged = mergeRestoredComposerFiles(local, pending);
  assert.equal(merged.filter((f) => f.attachmentId === "att-1").length, 1);
  assert.equal(merged.some((f) => f.state === "uploading"), true);
  assert.equal(merged.some((f) => f.state === "error"), true);
});

test("stale empty pending GET must not remove a newly uploaded chip", () => {
  const current: ComposerFileChip[] = [
    {
      localId: "local-1",
      name: "first.txt",
      state: "uploaded",
      attachmentId: "att-new",
      textExcerpt: "hello",
    },
    {
      localId: "up-2",
      name: "second.txt",
      state: "uploading",
    },
    {
      localId: "err-3",
      name: "bad.bin",
      state: "error",
      errorMessage: "Unsupported",
    },
  ];

  // Stale GET started before POST completed — empty pending list.
  const merged = mergePendingAttachments({
    current,
    serverPending: [],
  });
  assert.equal(merged.length, 3);
  assert.equal(merged[0]?.attachmentId, "att-new");
  assert.equal(merged[0]?.state, "uploaded");
  assert.equal(merged[1]?.state, "uploading");
  assert.equal(merged[2]?.state, "error");
});

test("pending GET adds missing server rows without duplicating local uploaded ids", () => {
  const current: ComposerFileChip[] = [
    {
      localId: "local-1",
      name: "first.txt",
      state: "uploaded",
      attachmentId: "att-1",
    },
  ];
  const merged = mergePendingAttachments({
    current,
    serverPending: [
      { id: "att-1", filename: "first.txt", text_excerpt: "from-server" },
      { id: "att-2", filename: "other.txt", text_excerpt: "other" },
    ],
  });
  assert.equal(merged.length, 2);
  assert.equal(merged[0]?.attachmentId, "att-1");
  assert.equal(merged[0]?.textExcerpt, undefined);
  assert.equal(merged[1]?.attachmentId, "att-2");
  assert.equal(merged[1]?.name, "other.txt");
});

test("stale pending restore generation is ignored", () => {
  assert.equal(
    shouldApplyPendingAttachmentRestore({
      requestedChatId: "chat-1",
      activeChatId: "chat-1",
      requestGeneration: 1,
      latestGeneration: 2,
    }),
    false,
  );
  assert.equal(
    shouldApplyPendingAttachmentRestore({
      requestedChatId: "chat-1",
      activeChatId: "chat-2",
      requestGeneration: 2,
      latestGeneration: 2,
    }),
    false,
  );
  assert.equal(
    shouldApplyPendingAttachmentRestore({
      requestedChatId: "chat-1",
      activeChatId: "chat-1",
      requestGeneration: 3,
      latestGeneration: 3,
    }),
    true,
  );
});

test("apiErrorMessage prefers Error.message", () => {
  assert.equal(apiErrorMessage(new Error("File exceeds maximum size of 10 MB.")), "File exceeds maximum size of 10 MB.");
  assert.equal(apiErrorMessage("plain"), "plain");
});

test("resolveFailedResponseMessage reads message field", () => {
  assert.equal(
    resolveFailedResponseMessage(
      { error: "INVALID_ATTACHMENT", message: "Invalid or corrupt DOCX file" },
      "fallback",
    ),
    "Invalid or corrupt DOCX file",
  );
});

test("resolveFailedResponseMessage reads detail string", () => {
  assert.equal(
    resolveFailedResponseMessage({ detail: "File exceeds maximum size of 10 MB." }, "fallback"),
    "File exceeds maximum size of 10 MB.",
  );
});

test("resolveFailedResponseMessage reads plain string body", () => {
  assert.equal(resolveFailedResponseMessage("Server exploded", "fallback"), "Server exploded");
});

test("resolveFailedResponseMessage falls back for unknown bodies", () => {
  assert.equal(resolveFailedResponseMessage(undefined, "Request failed"), "Request failed");
  assert.equal(resolveFailedResponseMessage({ foo: 1 }, "Request failed"), "Request failed");
});

test("removing uploaded chip requires remote DELETE", () => {
  const uploaded = {
    localId: "1",
    name: "a.txt",
    state: "uploaded" as const,
    attachmentId: "att-1",
  };
  const failed = {
    localId: "2",
    name: "b.txt",
    state: "error" as const,
    errorMessage: "boom",
  };
  const localOnly = { localId: "3", name: "c.txt", state: "uploading" as const };
  assert.equal(shouldDeleteComposerAttachmentRemotely(uploaded), true);
  assert.equal(shouldDeleteComposerAttachmentRemotely(failed), false);
  assert.equal(shouldDeleteComposerAttachmentRemotely(localOnly), false);
});

test("delete failure keeps chip and shows error", () => {
  const files = [
    {
      localId: "1",
      name: "a.txt",
      state: "uploaded" as const,
      attachmentId: "att-1",
      deleting: true,
    },
  ];
  const next = setComposerFileDeleteError(files, "1", "Attachment is linked to a turn");
  assert.equal(next.length, 1);
  assert.equal(next[0].attachmentId, "att-1");
  assert.equal(next[0].deleting, false);
  assert.equal(next[0].errorMessage, "Attachment is linked to a turn");
});

test("repeated delete clicks are blocked while deleting", () => {
  const idle = {
    localId: "1",
    name: "a.txt",
    state: "uploaded" as const,
    attachmentId: "att-1",
  };
  const busy = markComposerFileDeleting([idle], "1", true)[0];
  assert.equal(canStartComposerAttachmentDelete(idle), true);
  assert.equal(canStartComposerAttachmentDelete(busy), false);
  assert.deepEqual(removeComposerFileByLocalId([busy], "1"), []);
});

test("pending attachment count is capped at 10", () => {
  const files = Array.from({ length: 10 }, (_, i) => ({
    localId: String(i),
    name: `${i}.txt`,
    state: "uploaded" as const,
    attachmentId: `att-${i}`,
  }));
  assert.equal(countActiveComposerAttachments(files), 10);
  assert.equal(canAcceptMoreComposerAttachments(files), false);
  assert.equal(canAcceptMoreComposerAttachments(files.slice(0, 9)), true);
});

test("first file change captures files before reset so async upload still sees them", () => {
  const first = { name: "notes.txt", size: 12 } as File;
  let liveFiles: File[] | null = [first];
  const input = {
    get files() {
      return liveFiles as unknown as FileList | null;
    },
    set value(next: string) {
      if (next === "") liveFiles = [];
    },
    get value() {
      return liveFiles?.length ? "C:\\fakepath\\notes.txt" : "";
    },
  };

  const captured = captureComposerFileInputFiles(input);
  assert.equal(captured.length, 1);
  assert.equal(captured[0].name, "notes.txt");
  assert.equal(input.value, "");
  assert.deepEqual(liveFiles, []);

  // Same file can be selected again after reset.
  liveFiles = [first];
  const again = captureComposerFileInputFiles(input);
  assert.equal(again.length, 1);
  assert.equal(again[0].name, "notes.txt");
});

test("upload menu opens picker before closing and does not require a second click", () => {
  const order: string[] = [];
  const input = {
    accept: "",
    click() {
      order.push("picker");
    },
  } as unknown as HTMLInputElement;

  const opened = triggerComposerUploadFromMenu(input, () => {
    order.push("menu-closed");
  });

  assert.equal(opened, true);
  assert.equal(input.accept, COMPOSER_FILE_ACCEPT);
  assert.deepEqual(order, ["picker", "menu-closed"]);
  assert.equal(openComposerFilePicker(null), false);
});

test("chat-switch clear is skipped only while retaining the newly created chat", () => {
  assert.equal(
    shouldClearComposerFilesOnChatChange({ nextChatId: null, retainForChatId: null }),
    true,
  );
  assert.equal(
    shouldClearComposerFilesOnChatChange({
      nextChatId: "chat-new",
      retainForChatId: "chat-new",
    }),
    false,
  );
  assert.equal(
    shouldClearComposerFilesOnChatChange({
      nextChatId: "chat-b",
      retainForChatId: "chat-a",
    }),
    true,
  );
  assert.equal(
    shouldClearComposerFilesOnChatChange({ nextChatId: "chat-b", retainForChatId: null }),
    true,
  );
});

test("new-chat first selection creates chat, uploads once, and survives chat-switch clear", async () => {
  const file = { name: "first.txt", size: 8 } as File;
  const retainRef = { current: null as string | null };
  let activeChatId: string | null = null;
  let files: ComposerFileChip[] = [];
  const uploadCalls: string[] = [];
  const order: string[] = [];
  let createCalls = 0;
  let activateCalls = 0;

  const result = await runComposerUploads([file], {
    activeChatId: null,
    retainRef,
    getActiveChatId: () => activeChatId,
    getFiles: () => files,
    setFiles: (updater) => {
      files = updater(files);
    },
    createChat: async ({ activate = true, onChatCreated } = {}) => {
      createCalls += 1;
      const id = "chat-created-1";
      onChatCreated?.(id);
      assert.equal(retainRef.current, id);
      order.push("created");
      if (activate !== false) {
        activeChatId = id;
        order.push("activated-in-create");
      }
      return id;
    },
    activateChat: (id) => {
      activateCalls += 1;
      order.push("upload-started-before-activate");
      assert.equal(uploadCalls.length, 1, "attachment POST must start before activate");
      activeChatId = id;
      if (
        shouldClearComposerFilesOnChatChange({
          nextChatId: activeChatId,
          retainForChatId: retainRef.current,
        })
      ) {
        files = [];
      }
      order.push("activated");
    },
    uploadAttachment: async (chatId, uploadedFile) => {
      order.push("upload");
      uploadCalls.push(`${chatId}:${uploadedFile.name}`);
      return { id: "att-1", text_excerpt: "hello" };
    },
  });

  assert.equal(result.targetChatId, "chat-created-1");
  assert.equal(result.uploadAttempts, 1);
  assert.equal(createCalls, 1);
  assert.equal(activateCalls, 1);
  assert.deepEqual(uploadCalls, ["chat-created-1:first.txt"]);
  assert.equal(files.length, 1);
  assert.equal(files[0].state, "uploaded");
  assert.equal(files[0].attachmentId, "att-1");
  assert.equal(retainRef.current, null);
  assert.deepEqual(order, [
    "created",
    "upload",
    "upload-started-before-activate",
    "activated",
  ]);
});

test("existing-chat upload does not create a chat and still uploads", async () => {
  const file = { name: "existing.txt", size: 4 } as File;
  const retainRef = { current: null as string | null };
  let files: ComposerFileChip[] = [];
  let createCalls = 0;
  const result = await runComposerUploads([file], {
    activeChatId: "chat-existing",
    retainRef,
    getActiveChatId: () => "chat-existing",
    getFiles: () => files,
    setFiles: (updater) => {
      files = updater(files);
    },
    createChat: async () => {
      createCalls += 1;
      return "should-not-run";
    },
    uploadAttachment: async (chatId) => ({ id: `att-${chatId}`, text_excerpt: null }),
  });
  assert.equal(createCalls, 0);
  assert.equal(result.uploadAttempts, 1);
  assert.equal(files[0]?.attachmentId, "att-chat-existing");
});

test("switching to another existing chat still clears chips", () => {
  assert.equal(
    shouldClearComposerFilesOnChatChange({
      nextChatId: "chat-b",
      retainForChatId: null,
    }),
    true,
  );
});

test("auto-discard allowed for exclusive failed first send with no upload", () => {
  assert.equal(
    shouldSkipAutoDiscardUnusedChat({
      chatId: "chat-new",
      retainForChatId: null,
      files: [],
    }),
    false,
  );
});

test("auto-discard skipped while upload retains the shared chat", () => {
  assert.equal(
    shouldSkipAutoDiscardUnusedChat({
      chatId: "chat-new",
      retainForChatId: "chat-new",
      files: [],
    }),
    true,
  );
});

test("auto-discard skipped while upload chips are in progress", () => {
  assert.equal(
    shouldSkipAutoDiscardUnusedChat({
      chatId: "chat-new",
      retainForChatId: null,
      files: [{ localId: "1", name: "a.txt", state: "uploading" }],
    }),
    true,
  );
});

test("auto-discard skipped when attachment already persisted on chips", () => {
  assert.equal(
    shouldSkipAutoDiscardUnusedChat({
      chatId: "chat-new",
      retainForChatId: null,
      files: [
        {
          localId: "1",
          name: "a.txt",
          state: "uploaded",
          attachmentId: "att-1",
        },
      ],
    }),
    true,
  );
});

test("auto-discard still allowed when retain points at a different chat", () => {
  assert.equal(
    shouldSkipAutoDiscardUnusedChat({
      chatId: "chat-new",
      retainForChatId: "chat-other",
      files: [],
    }),
    false,
  );
});
