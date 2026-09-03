import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "../..");
const chatSrc = readFileSync(join(root, "src/routes/chat.tsx"), "utf8");
const composerSrc = readFileSync(join(root, "src/components/chat/ChatComposer.tsx"), "utf8");

test("ChatPage does not own composer typing state", () => {
  assert.doesNotMatch(chatSrc, /const \[input, setInput\]/);
  assert.doesNotMatch(chatSrc, /updateComposerInput/);
  assert.match(chatSrc, /composerRef = useRef<ChatComposerHandle>/);
  assert.match(composerSrc, /const \[input, setInput\] = useState\(""\)/);
  assert.match(composerSrc, /onChange=\{\(event\) => persistValue\(event\.target\.value\)\}/);
});

test("draft restore is keyed to the active chat inside ChatComposer", () => {
  assert.match(composerSrc, /setInput\(readComposerDraft\(draftStorageKey\)\)/);
  assert.match(composerSrc, /\}, \[draftStorageKey\]\)/);
  assert.match(chatSrc, /draftStorageKey=\{draftStorageKey\}/);
});

test("send reads the live composer value and clears without dropping the draft until success", () => {
  assert.match(chatSrc, /composerRef\.current\?\.getValue\(\)\.trim\(\)/);
  assert.match(chatSrc, /composerRef\.current\?\.replaceValue\(""\)/);
  assert.match(chatSrc, /localStorage\.removeItem\(`multimind:draft:\$\{hadActiveChat \? chatId : "new"\}`\)/);
  assert.match(chatSrc, /composerRef\.current\?\.replaceValue\(question\)/);
});

test("stop restore, prompt builder, and transcription still command the composer through the ref", () => {
  assert.match(chatSrc, /composerRef\.current\?\.restoreAfterStop\(activeTurn\.user_message\)/);
  assert.match(chatSrc, /setPromptBuilderSeed\(composerRef\.current\?\.getValue\(\) \?\? ""\)/);
  assert.match(chatSrc, /onUse=\{\(text\) => composerRef\.current\?\.setValue\(text\)\}/);
  assert.match(chatSrc, /insertTranscriptIntoComposer/);
  assert.match(composerSrc, /insertTranscript:/);
  assert.match(composerSrc, /writeComposerDraft\(draftStorageKeyRef\.current, next\.value\)/);
});

test("Ctrl\/Cmd+Enter send stays in the composer and does not update ChatPage input state", () => {
  assert.match(composerSrc, /event\.key === "Enter" && \(event\.ctrlKey \|\| event\.metaKey\)/);
  assert.doesNotMatch(chatSrc, /onComposerKeyDown/);
});
