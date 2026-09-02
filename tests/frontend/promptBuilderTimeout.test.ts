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
    modal.indexOf("async function send()"),
    modal.indexOf("function reset()"),
  );
  assert.match(sendFn, /const next = beginPromptBuilderSend\(session, session\.draft\)/);
  assert.match(sendFn, /setSession\(next\)/);
  assert.match(sendFn, /persistPromptBuilderSession\(requestStorageKey, next\)/);
  assert.ok(sendFn.indexOf("setSession(next)") < sendFn.indexOf("api.promptBuilder"));
  assert.ok(
    sendFn.indexOf("persistPromptBuilderSession(requestStorageKey, next)") <
      sendFn.indexOf("api.promptBuilder"),
  );
  assert.match(
    sendFn,
    /setSession\(applyPromptBuilderSuccess\(next, response\.improved_prompt\)\)/,
  );
  assert.match(sendFn, /catch \(caught\)/);
  assert.doesNotMatch(sendFn, /setSession\(session\)/);
});
