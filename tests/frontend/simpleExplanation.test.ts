import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import type { ApiVerdict } from "../../src/lib/api/types.ts";
import {
  simpleExplanationSurface,
  visibleSimpleExplanationContent,
} from "../../src/lib/simpleExplanation.ts";

const root = join(dirname(fileURLToPath(import.meta.url)), "../..");
const chatSrc = readFileSync(join(root, "src/routes/chat.tsx"), "utf8");
const sharedSrc = readFileSync(join(root, "src/routes/shared.$token.tsx"), "utf8");
const boxSrc = readFileSync(join(root, "src/components/chat/SimpleExplanationBox.tsx"), "utf8");
const runnerSrc = readFileSync(join(root, "src/lib/turnRunner.ts"), "utf8");

function verdict(overrides: Partial<ApiVerdict> = {}): ApiVerdict {
  return {
    id: "v1",
    model_id: "gemini",
    strategy: "Synthesize",
    text: "Canonical verdict",
    reason: "Because",
    saved: false,
    tokens_input: 1,
    tokens_output: 1,
    simple_explanation: null,
    ...overrides,
  };
}

test("visibleSimpleExplanationContent requires succeeded non-empty content", () => {
  assert.equal(visibleSimpleExplanationContent(null), null);
  assert.equal(
    visibleSimpleExplanationContent(
      verdict({
        simple_explanation: {
          id: "e1",
          content: "Main point: Option B.",
          model_id: "gpt-4.1-mini",
          status: "succeeded",
        },
      }),
    ),
    "Main point: Option B.",
  );
  assert.equal(
    visibleSimpleExplanationContent(
      verdict({
        simple_explanation: {
          id: "e1",
          content: "Main point: Option B.",
          model_id: "gpt-4.1-mini",
          status: "failed",
        },
      }),
    ),
    null,
  );
  assert.equal(visibleSimpleExplanationContent(verdict({ simple_explanation: null })), null);
  assert.equal(
    visibleSimpleExplanationContent(
      verdict({
        simple_explanation: {
          id: "e1",
          content: "   ",
          model_id: "gpt-4.1-mini",
          status: "succeeded",
        },
      }),
    ),
    null,
  );
});

test("live Simplifier working shows loading surface", () => {
  assert.equal(simpleExplanationSurface(verdict({ simple_explanation: null }), { pollActive: true }), "loading");
});

test("persisted pending shows loading surface", () => {
  assert.equal(
    simpleExplanationSurface(
      verdict({
        simple_explanation: {
          id: "e1",
          content: null,
          model_id: "gpt-4.1-mini",
          status: "pending",
        },
      }),
    ),
    "loading",
  );
});

test("succeeded shows ready surface without needing poll", () => {
  assert.equal(
    simpleExplanationSurface(
      verdict({
        simple_explanation: {
          id: "e1",
          content: "Main point: Option B.",
          model_id: "gpt-4.1-mini",
          status: "succeeded",
        },
      }),
      { pollActive: true },
    ),
    "ready",
  );
});

test("failed hides Simple Explanation surface", () => {
  assert.equal(
    simpleExplanationSurface(
      verdict({
        simple_explanation: {
          id: "e1",
          content: "oops",
          model_id: "gpt-4.1-mini",
          status: "failed",
        },
      }),
      { pollActive: true },
    ),
    "none",
  );
});

test("polling timeout with no terminal status hides loader", () => {
  assert.equal(
    simpleExplanationSurface(verdict({ simple_explanation: null }), { pollActive: false }),
    "none",
  );
  assert.equal(
    simpleExplanationSurface(
      verdict({
        simple_explanation: {
          id: "e1",
          content: null,
          model_id: "gpt-4.1-mini",
          status: "pending",
        },
      }),
      { pollTimedOut: true },
    ),
    "none",
  );
});

test("historical Verdict with null explanation does not show a loader", () => {
  assert.equal(simpleExplanationSurface(verdict({ simple_explanation: null })), "none");
});

test("no Verdict shows neither loader nor explanation", () => {
  assert.equal(simpleExplanationSurface(null), "none");
  assert.equal(simpleExplanationSurface(undefined), "none");
});

test("chat AiTurn renders Simple Explanation after Verdict and before Challenge", () => {
  const verdictIdx = chatSrc.indexOf("{verdictBlock}");
  const explIdx = chatSrc.indexOf("{simpleExplanationBlock}");
  const challengeIdx = chatSrc.indexOf("<VerdictDisagreeChat");
  assert.ok(verdictIdx > 0);
  assert.ok(explIdx > verdictIdx);
  assert.ok(challengeIdx > explIdx);
  assert.match(chatSrc, /simpleExplanationSurface\(turn\.verdict/);
  assert.match(chatSrc, /SimpleExplanationLoadingBox/);
  assert.match(chatSrc, /simple_explanation_poll_active/);
});

test("shared chat renders Simple Explanation under Verdict", () => {
  const verdictIdx = sharedSrc.indexOf("{verdictBlock}");
  const explIdx = sharedSrc.indexOf("{simpleExplanationBlock}");
  assert.ok(verdictIdx > 0);
  assert.ok(explIdx > verdictIdx);
  assert.match(sharedSrc, /simpleExplanationSurface\(turn\.verdict\)/);
  assert.match(sharedSrc, /SimpleExplanationLoadingBox/);
});

test("simple explanation box has no Verdict actions", () => {
  assert.match(boxSrc, /Simple Explanation/);
  assert.match(boxSrc, /data-testid="simple-explanation"/);
  assert.match(boxSrc, /data-testid="simple-explanation-loading"/);
  assert.match(boxSrc, /Making this easier to understand/);
  assert.match(boxSrc, /This usually takes a few seconds/);
  assert.match(boxSrc, /animate-spin/);
  assert.doesNotMatch(boxSrc, /onTogglePin|Challenge|VerdictCopyButton|Bookmark/);
});

test("live poll flag is set for Verdict and cleared when polling ends", () => {
  assert.match(runnerSrc, /explanationPolling/);
  assert.match(runnerSrc, /simple_explanation_poll_active: explanationPolling\.has\(turn\.id\)/);
  assert.match(runnerSrc, /event === "verdict_completed" && next\.verdict/);
  assert.match(runnerSrc, /event === "turn_completed" && next\.verdict/);
  assert.match(runnerSrc, /setExplanationPolling\(chatId, turnId, false\)/);
  assert.match(runnerSrc, /explanationPollTimedOut\.add\(turnId\)/);
  assert.match(runnerSrc, /const maxAttempts = 15/);
  assert.match(runnerSrc, /const pollIntervalMs = 1000/);
});
