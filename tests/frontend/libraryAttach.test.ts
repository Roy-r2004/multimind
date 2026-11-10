import assert from "node:assert/strict";
import test from "node:test";

import type { ApiChatAttachment } from "../../src/lib/api/types.ts";
import type { ComposerFileChip } from "../../src/lib/composerAttachments.ts";
import {
  beginComposerUploadRetention,
  endComposerUploadRetention,
  mergePendingAttachments,
  shouldClearComposerFilesOnChatChange,
  shouldSkipAutoDiscardUnusedChat,
} from "../../src/lib/composerAttachments.ts";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import {
  attachLibraryItemsViaApi,
  attachLibraryItemToComposer,
  attachLibraryItemViaApi,
  computeLibraryBulkAttachLimit,
  findDuplicateLibraryAttachment,
  saveLibraryDocumentBeforeAttach,
  shouldAbortAttachForStaleEdits,
} from "../../src/lib/libraryAttach.ts";
import { COMPOSER_ATTACHMENT_MAX_COUNT } from "../../src/lib/composerAttachments.ts";
import { buildLibraryFolderTree, flattenLibraryFolderOptions } from "../../src/lib/libraryUi.ts";

const root = join(dirname(fileURLToPath(import.meta.url)), "../..");
const itemPageSrc = readFileSync(join(root, "src/routes/library.$itemId.tsx"), "utf8");
const indexSrc = readFileSync(join(root, "src/routes/library.index.tsx"), "utf8");

test("findDuplicateLibraryAttachment detects uploading or uploaded library chips", () => {
  const files: ComposerFileChip[] = [
    { localId: "1", name: "a", state: "uploaded", attachmentId: "att-1", libraryItemId: "lib-1" },
    { localId: "2", name: "b", state: "uploading", libraryItemId: "lib-2" },
  ];
  assert.equal(findDuplicateLibraryAttachment(files, "lib-1")?.localId, "1");
  assert.equal(findDuplicateLibraryAttachment(files, "lib-2")?.localId, "2");
  assert.equal(findDuplicateLibraryAttachment(files, "lib-3"), undefined);
});

test("save-before-attach stops when saving dirty document fails", async () => {
  let saves = 0;
  const allowed = await saveLibraryDocumentBeforeAttach(true, async () => {
    saves += 1;
    return false;
  });
  assert.equal(allowed, false);
  assert.equal(saves, 1);

  const cleanAllowed = await saveLibraryDocumentBeforeAttach(false, async () => {
    saves += 1;
    return false;
  });
  assert.equal(cleanAllowed, true);
  assert.equal(saves, 1);
});

test("attachLibraryItemToComposer attaches to an active chat without creating another", async () => {
  let files: ComposerFileChip[] = [];
  const retainRef = { current: null as string | null };
  let created = 0;
  const result = await attachLibraryItemToComposer("lib-9", "Austria Research", {
    activeChatId: "chat-1",
    createChat: async () => {
      created += 1;
      return "chat-new";
    },
    activateChat: () => {
      throw new Error("should not activate when chat already active");
    },
    retainRef,
    getFiles: () => files,
    setFiles: (updater) => {
      files = updater(files);
    },
    attachFromLibrary: async (chatId, libraryItemId) => {
      assert.equal(chatId, "chat-1");
      assert.equal(libraryItemId, "lib-9");
      return {
        id: "att-9",
        filename: "Austria Research.txt",
        content_type: "text/plain",
        size_bytes: 12,
        text_excerpt: "body",
        excerpt_status: "ready",
        library_item_id: "lib-9",
      };
    },
  });

  assert.equal(created, 0);
  assert.equal(result.createdNewChat, false);
  assert.equal(result.attachment?.id, "att-9");
  assert.equal(files.length, 1);
  assert.equal(files[0]?.state, "uploaded");
  assert.equal(files[0]?.libraryItemId, "lib-9");
  assert.equal(files[0]?.attachmentId, "att-9");
  assert.equal(retainRef.current, null);
});

test("attachLibraryItemToComposer retains chips across new-chat creation", async () => {
  let files: ComposerFileChip[] = [
    { localId: "keep", name: "existing.txt", state: "uploaded", attachmentId: "att-old" },
  ];
  const retainRef = { current: null as string | null };
  let activeChatId: string | null = null;
  let activated: string | null = null;

  const result = await attachLibraryItemToComposer("lib-new", "Meeting Notes", {
    activeChatId,
    createChat: async (options) => {
      const chatId = "chat-created";
      options?.onChatCreated?.(chatId);
      assert.equal(retainRef.current, chatId);
      assert.equal(
        shouldClearComposerFilesOnChatChange({
          previousChatId: null,
          nextChatId: chatId,
          retainForChatId: retainRef.current,
        }),
        false,
      );
      return chatId;
    },
    activateChat: (chatId) => {
      activated = chatId;
      activeChatId = chatId;
    },
    retainRef,
    getFiles: () => files,
    setFiles: (updater) => {
      files = updater(files);
    },
    attachFromLibrary: async (chatId) => {
      assert.equal(chatId, "chat-created");
      // Attach POST happens before activate — retention still held.
      assert.equal(retainRef.current, "chat-created");
      return {
        id: "att-lib",
        filename: "Meeting Notes.txt",
        content_type: "text/plain",
        size_bytes: 4,
        text_excerpt: "note",
        excerpt_status: "ready",
        library_item_id: "lib-new",
      };
    },
  });

  assert.equal(result.createdNewChat, true);
  assert.equal(activated, "chat-created");
  assert.equal(files.length, 2);
  assert.equal(files[0]?.attachmentId, "att-old");
  assert.equal(files[1]?.libraryItemId, "lib-new");
  assert.equal(
    shouldSkipAutoDiscardUnusedChat({
      chatId: "chat-created",
      retainForChatId: null,
      files,
    }),
    true,
  );
});

test("attachLibraryItemToComposer skips duplicate library item", async () => {
  let files: ComposerFileChip[] = [
    {
      localId: "1",
      name: "Austria Research",
      state: "uploaded",
      attachmentId: "att-1",
      libraryItemId: "lib-1",
    },
  ];
  let attachCalls = 0;
  const result = await attachLibraryItemToComposer("lib-1", "Austria Research", {
    activeChatId: "chat-1",
    createChat: async () => "x",
    activateChat: () => undefined,
    retainRef: { current: null },
    getFiles: () => files,
    setFiles: (updater) => {
      files = updater(files);
    },
    attachFromLibrary: async () => {
      attachCalls += 1;
      throw new Error("should not call");
    },
  });
  assert.equal(attachCalls, 0);
  assert.equal(result.attachment?.id, "att-1");
  assert.equal(files.length, 1);
});

test("attachLibraryItemViaApi creates chat then attaches before activate", async () => {
  const retainRef = { current: null as string | null };
  const events: string[] = [];
  const result = await attachLibraryItemViaApi({
    libraryItemId: "lib-42",
    activeChatId: null,
    createChat: async (options) => {
      events.push("create");
      options?.onChatCreated?.("chat-42");
      beginComposerUploadRetention(retainRef, "chat-42");
      return "chat-42";
    },
    activateChat: (chatId) => {
      events.push(`activate:${chatId}`);
      endComposerUploadRetention(retainRef, chatId);
    },
    retainRef,
    attachFromLibrary: async (chatId, libraryItemId) => {
      events.push(`attach:${chatId}:${libraryItemId}`);
      assert.equal(retainRef.current, "chat-42");
      return {
        id: "att-42",
        filename: "doc.txt",
        content_type: "text/plain",
        size_bytes: 1,
        text_excerpt: "x",
        excerpt_status: "ready",
        library_item_id: libraryItemId,
      };
    },
  });
  assert.deepEqual(events, ["create", "attach:chat-42:lib-42", "activate:chat-42"]);
  assert.equal(result.attachment?.id, "att-42");
  assert.equal(result.createdNewChat, true);
});

test("mergePendingAttachments preserves existing chips and skips duplicate library ids", () => {
  const current: ComposerFileChip[] = [
    {
      localId: "local",
      name: "Austria Research",
      state: "uploaded",
      attachmentId: "att-local",
      libraryItemId: "lib-1",
    },
  ];
  const merged = mergePendingAttachments({
    current,
    serverPending: [
      {
        id: "att-server",
        filename: "Austria Research.txt",
        text_excerpt: "body",
        library_item_id: "lib-1",
      },
      {
        id: "att-other",
        filename: "other.txt",
        text_excerpt: "y",
        library_item_id: "lib-2",
      },
    ],
  });
  assert.equal(merged.length, 2);
  assert.equal(merged[0]?.attachmentId, "att-local");
  assert.equal(merged[1]?.attachmentId, "att-other");
  assert.equal(merged[1]?.libraryItemId, "lib-2");
});

test("buildLibraryFolderTree nests folders", () => {
  const tree = buildLibraryFolderTree([
    {
      id: "1",
      name: "MultiMind",
      parent_id: null,
      created_at: "",
      updated_at: "",
    },
    {
      id: "2",
      name: "Development",
      parent_id: "1",
      created_at: "",
      updated_at: "",
    },
    {
      id: "3",
      name: "Rehab",
      parent_id: null,
      created_at: "",
      updated_at: "",
    },
  ]);
  assert.equal(tree.length, 2);
  assert.equal(tree[0]?.name, "MultiMind");
  assert.equal(tree[0]?.children[0]?.name, "Development");
  assert.equal(tree[1]?.name, "Rehab");
});

test("flattenLibraryFolderOptions builds nested path labels", () => {
  const options = flattenLibraryFolderOptions([
    { id: "1", name: "Rehab Research", parent_id: null, created_at: "", updated_at: "" },
    { id: "2", name: "Austria", parent_id: "1", created_at: "", updated_at: "" },
    { id: "3", name: "Centers", parent_id: "2", created_at: "", updated_at: "" },
    { id: "4", name: "Personal", parent_id: null, created_at: "", updated_at: "" },
  ]);
  assert.deepEqual(
    options.map((o) => ({ id: o.id, label: o.label, depth: o.depth })),
    [
      { id: "4", label: "Personal", depth: 0 },
      { id: "1", label: "Rehab Research", depth: 0 },
      { id: "2", label: "Rehab Research / Austria", depth: 1 },
      { id: "3", label: "Rehab Research / Austria / Centers", depth: 2 },
    ],
  );
});

test("shouldAbortAttachForStaleEdits aborts only when newer edits are still unsaved", () => {
  assert.equal(shouldAbortAttachForStaleEdits(true), true);
  assert.equal(shouldAbortAttachForStaleEdits(false), false);
});

test("attachToChat re-checks dirty after save-before-attach and aborts before attaching", () => {
  // Guards the edit-during-save race: typing while a save-before-attach is in
  // flight must never let the attach proceed (and navigate) against a stale,
  // already-superseded snapshot.
  assert.match(
    itemPageSrc,
    /if \(!\(await saveLibraryDocumentBeforeAttach\(dirty, save\)\)\) return;\s*\n\s*if \(shouldAbortAttachForStaleEdits\(dirtyRef\.current\)\)/,
  );
  assert.match(itemPageSrc, /dirtyRef\.current = dirty;/);
});

// --- attachLibraryItemsViaApi (bulk attach) ---

function makeBulkDeps(overrides?: {
  activeChatId?: string | null;
  createChat?: (options?: {
    activate?: boolean;
    onChatCreated?: (chatId: string) => void;
  }) => Promise<string | null>;
  attachFromLibrary?: (chatId: string, libraryItemId: string) => Promise<ApiChatAttachment>;
  /** Defaults to "this chat has no pending attachments yet". */
  existingPendingAttachments?: ApiChatAttachment[];
  listPendingAttachments?: (chatId: string) => Promise<ApiChatAttachment[]>;
}) {
  const retainRef = { current: null as string | null };
  const activatedChatIds: string[] = [];
  const attachCalls: { chatId: string; libraryItemId: string }[] = [];
  const createChatCalls: number[] = [];
  const listPendingAttachmentsCalls: string[] = [];

  const attachFromLibrary =
    overrides?.attachFromLibrary ??
    (async (chatId: string, libraryItemId: string) => {
      attachCalls.push({ chatId, libraryItemId });
      return {
        id: `att-${libraryItemId}`,
        filename: `${libraryItemId}.txt`,
        content_type: "text/plain",
        size_bytes: 1,
        text_excerpt: "x",
        excerpt_status: "ready",
        library_item_id: libraryItemId,
      };
    });

  const createChat =
    overrides?.createChat ??
    (async (options) => {
      createChatCalls.push(1);
      const chatId = "chat-new";
      options?.onChatCreated?.(chatId);
      return chatId;
    });

  const listPendingAttachments =
    overrides?.listPendingAttachments ??
    (async (chatId: string) => {
      listPendingAttachmentsCalls.push(chatId);
      return overrides?.existingPendingAttachments ?? [];
    });

  return {
    deps: {
      activeChatId:
        overrides && "activeChatId" in overrides ? (overrides.activeChatId ?? null) : "chat-active",
      createChat,
      activateChat: (chatId: string) => activatedChatIds.push(chatId),
      retainRef,
      attachFromLibrary,
      listPendingAttachments,
    },
    activatedChatIds,
    attachCalls,
    createChatCalls,
    listPendingAttachmentsCalls,
  };
}

function pendingAttachment(
  libraryItemId: string | null,
  id = `att-${libraryItemId}`,
): ApiChatAttachment {
  return {
    id,
    filename: `${libraryItemId ?? "upload"}.txt`,
    content_type: "text/plain",
    size_bytes: 1,
    text_excerpt: "x",
    excerpt_status: "ready",
    library_item_id: libraryItemId,
  };
}

test("attachLibraryItemsViaApi: a single selected item attaches to the active chat", async () => {
  const { deps, attachCalls, createChatCalls } = makeBulkDeps();
  const result = await attachLibraryItemsViaApi({ ...deps, libraryItemIds: ["lib-1"] });
  assert.equal(createChatCalls.length, 0);
  assert.deepEqual(attachCalls, [{ chatId: "chat-active", libraryItemId: "lib-1" }]);
  assert.deepEqual(result.successIds, ["lib-1"]);
  assert.deepEqual(result.failedIds, []);
  assert.equal(result.chatId, "chat-active");
  assert.equal(result.createdNewChat, false);
});

test("attachLibraryItemsViaApi: multiple selected items all attach to the same single chat", async () => {
  const { deps, attachCalls } = makeBulkDeps();
  const ids = ["lib-1", "lib-2", "lib-3"];
  const result = await attachLibraryItemsViaApi({ ...deps, libraryItemIds: ids });
  assert.equal(attachCalls.length, 3);
  // Every call must target the one resolved chat — never a chat per item.
  assert.ok(attachCalls.every((call) => call.chatId === "chat-active"));
  assert.deepEqual(attachCalls.map((call) => call.libraryItemId).sort(), ids);
  assert.deepEqual(result.successIds.sort(), ids);
  assert.deepEqual(result.failedIds, []);
});

test("attachLibraryItemsViaApi: re-attaching items the chat already has still succeeds (backend dedupes by chat+item)", async () => {
  // The client never special-cases "already attached" — it always calls
  // through, and relies on the existing attach_from_library endpoint, which
  // updates the existing pending row instead of creating a duplicate.
  const { deps, attachCalls } = makeBulkDeps();
  const result = await attachLibraryItemsViaApi({
    ...deps,
    libraryItemIds: ["lib-already-attached", "lib-new"],
  });
  assert.equal(attachCalls.length, 2);
  assert.deepEqual(result.successIds.sort(), ["lib-already-attached", "lib-new"]);
});

test("attachLibraryItemsViaApi: selecting more than the attachment limit is rejected up front, with zero attach calls", async () => {
  const { deps, attachCalls, createChatCalls } = makeBulkDeps();
  const tooMany = Array.from({ length: COMPOSER_ATTACHMENT_MAX_COUNT + 1 }, (_, i) => `lib-${i}`);
  const messages: string[] = [];
  const result = await attachLibraryItemsViaApi({
    ...deps,
    libraryItemIds: tooMany,
    onTooMany: (message) => messages.push(message),
  });
  assert.equal(attachCalls.length, 0, "must not attempt any attach call");
  assert.equal(createChatCalls.length, 0, "must not even resolve/create a chat");
  assert.equal(messages.length, 1);
  assert.match(messages[0] ?? "", new RegExp(String(COMPOSER_ATTACHMENT_MAX_COUNT)));
  assert.deepEqual(result.successIds, []);
  assert.deepEqual(result.failedIds, tooMany);
});

test("attachLibraryItemsViaApi: with no active chat, creates exactly one chat and activates it once, after attaching", async () => {
  const { deps, attachCalls, createChatCalls, activatedChatIds } = makeBulkDeps({
    activeChatId: null,
  });
  const result = await attachLibraryItemsViaApi({ ...deps, libraryItemIds: ["lib-1", "lib-2"] });
  assert.equal(createChatCalls.length, 1, "exactly one chat created for the whole batch");
  assert.ok(attachCalls.every((call) => call.chatId === "chat-new"));
  assert.deepEqual(activatedChatIds, ["chat-new"]);
  assert.equal(result.createdNewChat, true);
  assert.equal(result.chatId, "chat-new");
});

test("attachLibraryItemsViaApi: reports a clear error and attaches nothing when a chat can't be created", async () => {
  const messages: string[] = [];
  const { deps, attachCalls } = makeBulkDeps({
    activeChatId: null,
    createChat: async () => null,
  });
  const result = await attachLibraryItemsViaApi({
    ...deps,
    libraryItemIds: ["lib-1", "lib-2"],
    onError: (message) => messages.push(message),
  });
  assert.equal(attachCalls.length, 0);
  assert.equal(messages.length, 1);
  assert.equal(result.chatId, null);
  assert.deepEqual(result.failedIds, ["lib-1", "lib-2"]);
});

test("attachLibraryItemsViaApi: partial per-item failures are reported individually, not as one all-or-nothing batch", async () => {
  const { deps } = makeBulkDeps({
    attachFromLibrary: async (chatId, libraryItemId) => {
      if (libraryItemId === "lib-bad") throw new Error("boom");
      return {
        id: `att-${libraryItemId}`,
        filename: `${libraryItemId}.txt`,
        content_type: "text/plain",
        size_bytes: 1,
        text_excerpt: "x",
        excerpt_status: "ready",
        library_item_id: libraryItemId,
      };
    },
  });
  const result = await attachLibraryItemsViaApi({
    ...deps,
    libraryItemIds: ["lib-good-1", "lib-bad", "lib-good-2"],
  });
  assert.deepEqual(result.successIds.sort(), ["lib-good-1", "lib-good-2"]);
  assert.deepEqual(result.failedIds, ["lib-bad"]);
});

test("attachLibraryItemsViaApi: activates a newly created chat even on partial success", async () => {
  const { deps, activatedChatIds } = makeBulkDeps({
    activeChatId: null,
    attachFromLibrary: async (chatId, libraryItemId) => {
      if (libraryItemId === "lib-bad") throw new Error("boom");
      return {
        id: `att-${libraryItemId}`,
        filename: `${libraryItemId}.txt`,
        content_type: "text/plain",
        size_bytes: 1,
        text_excerpt: "x",
        excerpt_status: "ready",
        library_item_id: libraryItemId,
      };
    },
  });
  await attachLibraryItemsViaApi({ ...deps, libraryItemIds: ["lib-good", "lib-bad"] });
  assert.deepEqual(activatedChatIds, ["chat-new"]);
});

test("attachLibraryItemsViaApi: an empty selection is a no-op (no chat, no calls)", async () => {
  const { deps, attachCalls, createChatCalls } = makeBulkDeps();
  const result = await attachLibraryItemsViaApi({ ...deps, libraryItemIds: [] });
  assert.equal(attachCalls.length, 0);
  assert.equal(createChatCalls.length, 0);
  assert.deepEqual(result, {
    chatId: "chat-active",
    createdNewChat: false,
    successIds: [],
    failedIds: [],
  });
});

test("existing single-item attachLibraryItemViaApi path is unaffected by the new bulk function", async () => {
  // Regression: the original single-item API path still creates a chat once
  // and attaches exactly once, independent of the new bulk helper.
  const retainRef = { current: null as string | null };
  const events: string[] = [];
  const result = await attachLibraryItemViaApi({
    libraryItemId: "lib-solo",
    activeChatId: null,
    createChat: async (options) => {
      events.push("create");
      options?.onChatCreated?.("chat-solo");
      return "chat-solo";
    },
    activateChat: (chatId) => events.push(`activate:${chatId}`),
    retainRef,
    attachFromLibrary: async (chatId, libraryItemId) => {
      events.push(`attach:${chatId}:${libraryItemId}`);
      return {
        id: "att-solo",
        filename: "solo.txt",
        content_type: "text/plain",
        size_bytes: 1,
        text_excerpt: "x",
        excerpt_status: "ready",
        library_item_id: libraryItemId,
      };
    },
  });
  assert.deepEqual(events, ["create", "attach:chat-solo:lib-solo", "activate:chat-solo"]);
  assert.equal(result.attachment?.id, "att-solo");
  assert.equal(result.createdNewChat, true);
});

test("the bulk toolbar's Attach to Chat button sits right after Clear and before Move, unchanged otherwise", () => {
  const toolbar = indexSrc.slice(
    indexSrc.indexOf("{selectedIds.size} selected"),
    indexSrc.indexOf("{selectedIds.size} selected") + 3000,
  );
  const clearIndex = toolbar.indexOf("Clear");
  const attachIndex = toolbar.indexOf("Attach to Chat");
  const moveMatch = />\s*Move\s*<\/Button>/.exec(toolbar);
  const moveIndex = moveMatch?.index ?? -1;
  assert.ok(clearIndex > -1 && attachIndex > -1 && moveIndex > -1);
  assert.ok(
    clearIndex < attachIndex && attachIndex < moveIndex,
    "expected order: Clear, Attach to Chat, Move",
  );
  // Same disabled/styling convention as every other bulk button — no new UI pattern introduced.
  const buttonStart = toolbar.lastIndexOf("<Button", attachIndex);
  const attachButtonBlock = toolbar.slice(buttonStart, attachIndex);
  assert.match(attachButtonBlock, /size="sm"/);
  assert.match(attachButtonBlock, /variant="outline"/);
  assert.match(attachButtonBlock, /disabled=\{bulkBusy\}/);
});

test("bulk attach reuses attachLibraryItemsViaApi, navigates to /chat only once, and never touches selection", () => {
  const fn = indexSrc.slice(
    indexSrc.indexOf("async function bulkAttachToChat"),
    indexSrc.indexOf("async function bulkMove"),
  );
  assert.match(fn, /attachLibraryItemsViaApi\(/);
  const navigateMatches = fn.match(/await navigate\(\{ to: "\/chat" \}\)/g);
  assert.equal(navigateMatches?.length, 1, "navigate to /chat must happen at most once per call");
  assert.doesNotMatch(fn, /setSelectedIds/);
});

test("bulk attach only navigates on complete success — any failure (even partial) keeps the user on Library", () => {
  const fn = indexSrc.slice(
    indexSrc.indexOf("async function bulkAttachToChat"),
    indexSrc.indexOf("async function bulkMove"),
  );
  assert.match(
    fn,
    /if \(outcome\.failed > 0\) return;\s*\n\s*await navigate\(\{ to: "\/chat" \}\);/,
  );
  // The old (buggy) "only skip navigation when everything failed" check must be gone.
  assert.doesNotMatch(fn, /if \(outcome\.succeeded === 0\) return;/);
});

// --- computeLibraryBulkAttachLimit (pure limit math) ---

test("computeLibraryBulkAttachLimit allows a selection that fits under the max with no existing attachments", () => {
  const check = computeLibraryBulkAttachLimit({
    selectedIds: ["a", "b", "c"],
    existingPendingLibraryItemIds: [],
    existingPendingCount: 0,
  });
  assert.deepEqual(check, { allowed: true, newCount: 3 });
});

test("computeLibraryBulkAttachLimit counts the chat's existing pending attachments against the limit", () => {
  // 8 existing + 3 new = 11 > 10: must be rejected, even though 3 alone is fine.
  const check = computeLibraryBulkAttachLimit({
    selectedIds: ["a", "b", "c"],
    existingPendingLibraryItemIds: [],
    existingPendingCount: 8,
    maxPendingAttachments: 10,
  });
  assert.equal(check.allowed, false);
  assert.equal(check.newCount, 3);
  if (!check.allowed) {
    assert.match(check.message, /already has 8 pending attachments/);
    assert.match(check.message, /3 more/);
    assert.match(check.message, /limit of 10/);
  }
});

test("computeLibraryBulkAttachLimit does not count a selected item that's already pending as a new slot", () => {
  // The chat already has "a" and "b" pending; re-selecting them plus one new
  // item "c" only adds 1 new slot, not 3.
  const check = computeLibraryBulkAttachLimit({
    selectedIds: ["a", "b", "c"],
    existingPendingLibraryItemIds: ["a", "b"],
    existingPendingCount: 9,
    maxPendingAttachments: 10,
  });
  assert.equal(check.allowed, true);
  assert.equal(check.newCount, 1);
});

test("computeLibraryBulkAttachLimit rejects when duplicates alone would still push a fresh id over the limit", () => {
  const check = computeLibraryBulkAttachLimit({
    selectedIds: ["a", "b", "new-1"],
    existingPendingLibraryItemIds: ["a", "b"],
    existingPendingCount: 10,
    maxPendingAttachments: 10,
  });
  assert.equal(check.allowed, false);
  assert.equal(check.newCount, 1);
});

test("computeLibraryBulkAttachLimit's message has no false 'already has' claim when the chat starts empty", () => {
  const check = computeLibraryBulkAttachLimit({
    selectedIds: Array.from({ length: 11 }, (_, i) => `lib-${i}`),
    existingPendingLibraryItemIds: [],
    existingPendingCount: 0,
    maxPendingAttachments: 10,
  });
  assert.equal(check.allowed, false);
  if (!check.allowed) {
    assert.doesNotMatch(check.message, /already has/);
    assert.match(check.message, /at most 10 items/);
  }
});

// --- attachLibraryItemsViaApi: existing pending attachments (the actual fix) ---

test("attachLibraryItemsViaApi: rejects up front when existing + new selection would exceed the limit, with zero attach calls", async () => {
  const existing = Array.from({ length: 9 }, (_, i) => pendingAttachment(`existing-${i}`));
  const { deps, attachCalls, listPendingAttachmentsCalls } = makeBulkDeps({
    existingPendingAttachments: existing,
  });
  const messages: string[] = [];
  const result = await attachLibraryItemsViaApi({
    ...deps,
    libraryItemIds: ["new-1", "new-2"], // 9 existing + 2 new = 11 > 10
    onTooMany: (message) => messages.push(message),
  });
  assert.equal(listPendingAttachmentsCalls.length, 1);
  assert.equal(attachCalls.length, 0, "must check the limit before attempting any attach");
  assert.equal(messages.length, 1);
  assert.match(messages[0] ?? "", /already has 9 pending attachments/);
  assert.deepEqual(result.failedIds, ["new-1", "new-2"]);
  assert.deepEqual(result.successIds, []);
});

test("attachLibraryItemsViaApi: allows the selection when existing + new fits exactly at the limit", async () => {
  const existing = Array.from({ length: 8 }, (_, i) => pendingAttachment(`existing-${i}`));
  const { deps, attachCalls } = makeBulkDeps({ existingPendingAttachments: existing });
  const result = await attachLibraryItemsViaApi({
    ...deps,
    libraryItemIds: ["new-1", "new-2"], // 8 + 2 = 10, exactly at the limit
  });
  assert.equal(attachCalls.length, 2);
  assert.deepEqual(result.successIds.sort(), ["new-1", "new-2"]);
});

test("attachLibraryItemsViaApi: re-selecting already-pending items doesn't count against the limit even when the chat is already full", async () => {
  // Chat is at the max (10), but 2 of those are the library items being
  // re-selected here with no other new items — nothing new is being added,
  // so this must be allowed (a reattach refreshes, it doesn't add a slot).
  const existing = [
    pendingAttachment("lib-a"),
    pendingAttachment("lib-b"),
    ...Array.from({ length: 8 }, (_, i) => pendingAttachment(`existing-${i}`)),
  ];
  const { deps, attachCalls } = makeBulkDeps({ existingPendingAttachments: existing });
  const result = await attachLibraryItemsViaApi({
    ...deps,
    libraryItemIds: ["lib-a", "lib-b"],
  });
  assert.equal(attachCalls.length, 2);
  assert.deepEqual(result.successIds.sort(), ["lib-a", "lib-b"]);
});

test("attachLibraryItemsViaApi: uploaded-file attachments (no library_item_id) still count toward the same limit", async () => {
  // The backend's limit covers every pending attachment, not just
  // library-based ones — a chat with 10 uploaded files must still block.
  const existing = Array.from({ length: 10 }, (_, i) => pendingAttachment(null, `upload-${i}`));
  const { deps, attachCalls } = makeBulkDeps({ existingPendingAttachments: existing });
  const messages: string[] = [];
  const result = await attachLibraryItemsViaApi({
    ...deps,
    libraryItemIds: ["new-1"],
    onTooMany: (message) => messages.push(message),
  });
  assert.equal(attachCalls.length, 0);
  assert.equal(messages.length, 1);
  assert.deepEqual(result.failedIds, ["new-1"]);
});

test("attachLibraryItemsViaApi: a failure while checking existing attachments is reported, not silently swallowed", async () => {
  const { deps, attachCalls } = makeBulkDeps({
    listPendingAttachments: async () => {
      throw new Error("network down");
    },
  });
  const messages: string[] = [];
  const result = await attachLibraryItemsViaApi({
    ...deps,
    libraryItemIds: ["lib-1"],
    onError: (message) => messages.push(message),
  });
  assert.equal(attachCalls.length, 0);
  assert.deepEqual(messages, ["network down"]);
  assert.deepEqual(result.failedIds, ["lib-1"]);
});

test("attachLibraryItemsViaApi: a brand-new chat never checks pending attachments (it has none by construction)", async () => {
  const { deps, listPendingAttachmentsCalls, createChatCalls } = makeBulkDeps({
    activeChatId: null,
  });
  await attachLibraryItemsViaApi({ ...deps, libraryItemIds: ["lib-1"] });
  assert.equal(listPendingAttachmentsCalls.length, 0);
  assert.equal(createChatCalls.length, 1);
});
