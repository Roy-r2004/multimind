import assert from "node:assert/strict";
import test from "node:test";

import type { ComposerFileChip } from "../../src/lib/composerAttachments.ts";
import {
  beginComposerUploadRetention,
  endComposerUploadRetention,
  mergePendingAttachments,
  shouldClearComposerFilesOnChatChange,
  shouldSkipAutoDiscardUnusedChat,
} from "../../src/lib/composerAttachments.ts";
import {
  attachLibraryItemToComposer,
  attachLibraryItemViaApi,
  findDuplicateLibraryAttachment,
} from "../../src/lib/libraryAttach.ts";
import { buildLibraryFolderTree, flattenLibraryFolderOptions } from "../../src/lib/libraryUi.ts";

test("findDuplicateLibraryAttachment detects uploading or uploaded library chips", () => {
  const files: ComposerFileChip[] = [
    { localId: "1", name: "a", state: "uploaded", attachmentId: "att-1", libraryItemId: "lib-1" },
    { localId: "2", name: "b", state: "uploading", libraryItemId: "lib-2" },
  ];
  assert.equal(findDuplicateLibraryAttachment(files, "lib-1")?.localId, "1");
  assert.equal(findDuplicateLibraryAttachment(files, "lib-2")?.localId, "2");
  assert.equal(findDuplicateLibraryAttachment(files, "lib-3"), undefined);
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
  assert.equal(shouldSkipAutoDiscardUnusedChat({
    chatId: "chat-created",
    retainForChatId: null,
    files,
  }), true);
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
