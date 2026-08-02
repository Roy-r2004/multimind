import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import {
  CHAT_TURN_LAYOUT_KEY,
  DEFAULT_CHAT_TURN_LAYOUT,
  __resetChatTurnLayoutSessionForTests,
  chatAnswerCardsClassName,
  clearChatTurnLayoutSession,
  isChatTurnLayout,
  parseChatTurnLayout,
  readChatTurnLayout,
  writeChatTurnLayout,
} from "../../src/lib/chatTurnLayout.ts";

const root = join(dirname(fileURLToPath(import.meta.url)), "../..");

function memoryStorage(): Storage {
  const map = new Map<string, string>();
  return {
    get length() {
      return map.size;
    },
    clear() {
      map.clear();
    },
    getItem(key: string) {
      return map.has(key) ? map.get(key)! : null;
    },
    key(index: number) {
      return [...map.keys()][index] ?? null;
    },
    removeItem(key: string) {
      map.delete(key);
    },
    setItem(key: string, value: string) {
      map.set(key, String(value));
    },
  };
}

function installStorage(storage: Storage | null) {
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: storage
      ? {
          localStorage: storage,
          addEventListener() {},
          removeEventListener() {},
          dispatchEvent() {
            return true;
          },
        }
      : undefined,
    writable: true,
  });
  if (storage) {
    Object.defineProperty(globalThis, "localStorage", {
      configurable: true,
      value: storage,
      writable: true,
    });
  }
}

test.beforeEach(() => {
  __resetChatTurnLayoutSessionForTests();
  installStorage(memoryStorage());
});

test("default layout is Vertical when no stored value exists", () => {
  assert.equal(DEFAULT_CHAT_TURN_LAYOUT, "vertical");
  assert.equal(readChatTurnLayout(), "vertical");
  assert.equal(parseChatTurnLayout(null), "vertical");
  assert.equal(parseChatTurnLayout(undefined), "vertical");
});

test("valid stored horizontal preference is restored", () => {
  window.localStorage.setItem(CHAT_TURN_LAYOUT_KEY, "horizontal");
  assert.equal(readChatTurnLayout(), "horizontal");
  assert.equal(parseChatTurnLayout("horizontal"), "horizontal");
  assert.equal(isChatTurnLayout("horizontal"), true);
});

test("invalid stored value falls back safely", () => {
  for (const bad of ["", "sideways", "VERTICAL", "grid", "1", "{}"]) {
    assert.equal(parseChatTurnLayout(bad), "vertical", bad);
  }
  window.localStorage.setItem(CHAT_TURN_LAYOUT_KEY, "nope");
  assert.equal(readChatTurnLayout(), "vertical");
});

test("writeChatTurnLayout persists selection to localStorage", () => {
  assert.equal(writeChatTurnLayout("horizontal"), true);
  assert.equal(window.localStorage.getItem(CHAT_TURN_LAYOUT_KEY), "horizontal");
  assert.equal(readChatTurnLayout(), "horizontal");

  assert.equal(writeChatTurnLayout("vertical"), true);
  assert.equal(window.localStorage.getItem(CHAT_TURN_LAYOUT_KEY), "vertical");
  assert.equal(readChatTurnLayout(), "vertical");
});

test("Horizontal and Vertical class selection differs and has no hard-coded four-card logic", () => {
  const vertical = chatAnswerCardsClassName("vertical");
  const horizontal = chatAnswerCardsClassName("horizontal");

  assert.match(vertical, /grid-cols-1/);
  assert.doesNotMatch(vertical, /auto-fit/);

  assert.match(horizontal, /auto-fit/);
  assert.match(horizontal, /minmax/);
  assert.doesNotMatch(horizontal, /grid-cols-4/);
  assert.notEqual(vertical, horizontal);

  // Class helpers are independent of answer count — no 4-card assumption.
  assert.equal(chatAnswerCardsClassName("horizontal"), horizontal);
});

test("storage failure does not crash; session preference still updates", () => {
  const boom: Storage = {
    get length() {
      return 0;
    },
    clear() {},
    getItem() {
      throw new Error("blocked");
    },
    key() {
      return null;
    },
    removeItem() {
      throw new Error("blocked");
    },
    setItem() {
      throw new Error("blocked");
    },
  };
  installStorage(boom);
  __resetChatTurnLayoutSessionForTests();

  assert.equal(readChatTurnLayout(), "vertical");
  assert.equal(writeChatTurnLayout("horizontal"), false);
  assert.equal(readChatTurnLayout(), "horizontal");
});

test("missing window / SSR path returns Vertical without throwing", () => {
  __resetChatTurnLayoutSessionForTests();
  installStorage(null);
  assert.equal(readChatTurnLayout(), "vertical");
  assert.equal(writeChatTurnLayout("horizontal"), false);
});

test("clearing session re-reads storage (other-tab style sync)", () => {
  writeChatTurnLayout("horizontal");
  window.localStorage.setItem(CHAT_TURN_LAYOUT_KEY, "vertical");
  // Session still wins until cleared.
  assert.equal(readChatTurnLayout(), "horizontal");
  clearChatTurnLayoutSession();
  assert.equal(readChatTurnLayout(), "vertical");
});

test("chat and shared routes wire layout helpers; shared stays read-only for edits", () => {
  const chatSrc = readFileSync(join(root, "src/routes/chat.tsx"), "utf8");
  const sharedSrc = readFileSync(join(root, "src/routes/shared.$token.tsx"), "utf8");
  const toggleSrc = readFileSync(
    join(root, "src/components/chat/ChatTurnLayoutToggle.tsx"),
    "utf8",
  );

  assert.match(chatSrc, /ChatTurnLayoutToggle/);
  assert.match(chatSrc, /chatAnswerCardsClassName/);
  assert.match(chatSrc, /data-chat-answer-layout/);
  assert.match(chatSrc, /useChatTurnLayout/);

  assert.match(sharedSrc, /ChatTurnLayoutToggle/);
  assert.match(sharedSrc, /chatAnswerCardsClassName/);
  assert.match(sharedSrc, /useChatTurnLayout/);
  assert.doesNotMatch(sharedSrc, /UserPromptBubble/);
  assert.doesNotMatch(sharedSrc, /canEditUserPrompt/);
  assert.doesNotMatch(sharedSrc, /regenerate/);

  // Loading + completed answer containers use the shared class helper; Verdict stays outside.
  assert.match(chatSrc, /data-testid="loading-answer-layout"/);
  assert.match(chatSrc, /data-testid="loading-verdict"/);
  assert.match(sharedSrc, /data-testid="shared-verdict"/);

  assert.match(toggleSrc, /aria-label="Answer layout"/);
  assert.match(toggleSrc, /aria-label="Vertical layout"/);
  assert.match(toggleSrc, /aria-label="Horizontal layout"/);
  assert.match(toggleSrc, /type="single"/);
});

test("stable storage key matches product convention", () => {
  assert.equal(CHAT_TURN_LAYOUT_KEY, "multimind.chatTurnLayout");
});
