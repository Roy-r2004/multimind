import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const root = join(dirname(fileURLToPath(import.meta.url)), "../..");

function readSrc(relativePath: string): string {
  return readFileSync(join(root, relativePath), "utf8");
}

test("default API request timeout is 60s, not 30s", () => {
  const clientSource = readSrc("src/lib/api/client.ts");
  assert.match(clientSource, /export const DEFAULT_TIMEOUT_MS = 60_000;/);
});

test("Prompt Builder refine allows council plus referee; improve stays a single-generation timeout", () => {
  const apiSource = readSrc("src/lib/api/index.ts");
  const builderBlock = apiSource.slice(
    apiSource.indexOf("promptBuilder:"),
    apiSource.indexOf("transcriptions:"),
  );

  assert.match(apiSource, /export const PROMPT_BUILDER_REQUEST_TIMEOUT_MS = 300_000;/);
  assert.match(apiSource, /export const PROMPT_BUILDER_REFINE_TIMEOUT_MS = 660_000;/);
  assert.ok(300_000 > 60_000);
  assert.ok(660_000 > 300_000);
  assert.ok(660_000 > 30_000);

  const refineBlock = builderBlock.slice(
    builderBlock.indexOf("refine:"),
    builderBlock.indexOf("context:"),
  );
  const improveBlock = builderBlock.slice(
    builderBlock.indexOf("improve:"),
    builderBlock.indexOf("refine:"),
  );
  const contextBlock = builderBlock.slice(builderBlock.indexOf("context:"));

  assert.match(refineBlock, /timeoutMs: PROMPT_BUILDER_REFINE_TIMEOUT_MS/);
  assert.doesNotMatch(refineBlock, /timeoutMs: PROMPT_BUILDER_REQUEST_TIMEOUT_MS/);
  assert.match(improveBlock, /timeoutMs: PROMPT_BUILDER_REQUEST_TIMEOUT_MS/);
  assert.doesNotMatch(improveBlock, /timeoutMs: PROMPT_BUILDER_REFINE_TIMEOUT_MS/);
  assert.match(refineBlock, /timeoutMessage: PROMPT_BUILDER_TIMEOUT_MESSAGE/);
  assert.match(improveBlock, /timeoutMessage: PROMPT_BUILDER_TIMEOUT_MESSAGE/);
  assert.doesNotMatch(contextBlock, /timeoutMs:/);
});

test("normal API calls keep the default timeout unless they opt in", () => {
  const apiSource = readSrc("src/lib/api/index.ts");
  const costsBlock = apiSource.slice(apiSource.indexOf("costs:"), apiSource.indexOf("admin:"));
  assert.doesNotMatch(costsBlock, /timeoutMs:/);

  const clientSource = readSrc("src/lib/api/client.ts");
  assert.match(clientSource, /timeoutMs: options\.timeoutMs \?\? DEFAULT_TIMEOUT_MS/);
});

test("Prompt Builder timeout message is specific and does not mention waking up or 30s", () => {
  const apiSource = readSrc("src/lib/api/index.ts");
  assert.match(
    apiSource,
    /Prompt Builder is taking longer than expected\. Your Builder session is saved\./,
  );
  const builderBlock = apiSource.slice(
    apiSource.indexOf("PROMPT_BUILDER_TIMEOUT_MESSAGE"),
    apiSource.indexOf("type Auth"),
  );
  assert.doesNotMatch(builderBlock, /waking up or redeploying/);
  assert.doesNotMatch(builderBlock, /wait ~30s/);
});

test("Prompt Builder modal captures originalPrompt and persists the first user send before refine", () => {
  const modal = readSrc("src/components/chat/PromptBuilderModal.tsx");
  const sendFn = modal.slice(
    modal.indexOf("async function send("),
    modal.indexOf("function requestNewSession()"),
  );
  assert.match(sendFn, /const next = beginPromptBuilderSend\(session, text\)/);
  assert.match(sendFn, /setSession\(next\)/);
  assert.match(sendFn, /persistPromptBuilderSession\(requestStorageKey, next\)/);
  assert.ok(sendFn.indexOf("setSession(next)") < sendFn.indexOf("api.promptBuilder"));
  assert.ok(
    sendFn.indexOf("persistPromptBuilderSession(requestStorageKey, next)") <
      sendFn.indexOf("api.promptBuilder"),
  );
  assert.match(sendFn, /applyPromptBuilderSuccess\(\s*current,/);
  assert.match(sendFn, /applyPromptBuilderClarification\(current, response\.assistant_message\)/);
  assert.match(sendFn, /if \(!stillCurrent\(\)\) return;/);
  assert.match(sendFn, /catch \(caught\)/);
  assert.doesNotMatch(sendFn, /setSession\(session\)/);
});

test("Prompt Builder send() and the context preview both guard against a superseded session generation", () => {
  const modal = readSrc("src/components/chat/PromptBuilderModal.tsx");
  assert.match(modal, /const generationRef = useRef\(0\)/);
  assert.match(modal, /generationRef\.current \+= 1/);
  assert.match(modal, /generationRef\.current === requestGeneration/);
  assert.match(modal, /promptRevisionRef\.current === requestPromptRevision/);
  assert.match(modal, /contextRequestRef\.current === requestId/);
});

test("New Session uses the app's own Modal confirmation, not window.confirm", () => {
  const modal = readSrc("src/components/chat/PromptBuilderModal.tsx");
  assert.doesNotMatch(modal, /window\.confirm/);
  assert.match(modal, /showNewSessionConfirm/);
  assert.match(modal, /title="Start a new session\?"/);
});

test("Final Prompt panel exposes Copy, Save to Library, and Use This Prompt", () => {
  const modal = readSrc("src/components/chat/PromptBuilderModal.tsx");
  assert.match(modal, /Final Prompt/);
  assert.match(modal, /Save to Library/);
  assert.match(modal, /Use This Prompt/);
  assert.match(modal, /applyManualPromptEdit\(current, event\.target\.value\)/);
  assert.match(modal, /<SavePromptDialog[\s\S]*?turnId=\{null\}/);
});

test("New Session clears loading/error state and bumps generation before replacing the session", () => {
  const modal = readSrc("src/components/chat/PromptBuilderModal.tsx");
  const performFn = modal.slice(
    modal.indexOf("function performNewSession()"),
    modal.indexOf("async function copyOriginalPrompt()"),
  );
  assert.match(performFn, /setShowNewSessionConfirm\(false\)/);
  assert.match(performFn, /generationRef\.current \+= 1/);
  assert.match(performFn, /setLoading\(false\)/);
  assert.match(performFn, /setError\(null\)/);
  // generationRef is bumped before the stale session is replaced, so an in-flight
  // refine() from the old session can never land on the new one (send()'s
  // stillCurrent() check reads the same ref).
  assert.ok(
    performFn.indexOf("generationRef.current += 1") <
      performFn.indexOf("setSession(replacement)"),
  );
});

test("hydrate (open/identity change) also bumps generation, invalidating in-flight requests from a closed or switched session", () => {
  const modal = readSrc("src/components/chat/PromptBuilderModal.tsx");
  const hydrateEffect = modal.slice(
    modal.indexOf("useLayoutEffect(() => {"),
    modal.indexOf("useEffect(() => {\n    if (hydratingKey.current"),
  );
  assert.match(hydrateEffect, /generationRef\.current \+= 1/);
  assert.match(hydrateEffect, /loadedKey\.current = storageKey/);
});

test("the context preview effect cancels its own stale timer and ignores out-of-order responses", () => {
  const modal = readSrc("src/components/chat/PromptBuilderModal.tsx");
  const effectBlock = modal.slice(
    modal.indexOf("const requestId = ++contextRequestRef.current;"),
    modal.indexOf("useEffect(() => {\n    if (!open || !listRef.current)"),
  );
  assert.match(effectBlock, /let cancelled = false/);
  assert.match(effectBlock, /cancelled = true/);
  assert.match(effectBlock, /window\.clearTimeout\(timer\)/);
  assert.match(effectBlock, /!cancelled &&/);
});

test("small screens get a Conversation/Final Prompt tab switcher that is hidden at the lg breakpoint", () => {
  const modal = readSrc("src/components/chat/PromptBuilderModal.tsx");
  assert.match(modal, /border-b border-border bg-accent\/5 px-3 py-2 lg:hidden/);
  assert.match(modal, /setMobileTab\("conversation"\)/);
  assert.match(modal, /setMobileTab\("final"\)/);
});

test("a clarifying question offers Generate without answering, which skips straight to a real prompt", () => {
  const modal = readSrc("src/components/chat/PromptBuilderModal.tsx");
  assert.match(modal, /GENERATE_WITHOUT_ANSWERING_TEXT/);
  assert.match(modal, /isClarifyingPending/);
  assert.match(
    modal,
    /session\.latestPrompt === null && hasGenerated && lastMessage\?\.role === "assistant"/,
  );
});
