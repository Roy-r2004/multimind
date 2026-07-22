import assert from "node:assert/strict";
import test from "node:test";

import { streamTurn } from "../../src/lib/api/stream.ts";
import {
  getChatTurns,
  removeTurn,
  resumeRunningTurns,
  seedChatTurns,
} from "../../src/lib/turnRunner.ts";

const auth = { token: "token", orgId: "org-1" };

function turn(overrides = {}) {
  return {
    id: "turn-1",
    chat_id: "chat-1",
    user_message: "Prompt",
    model_set_id: "set",
    strategy: "Synthesize",
    verdict_model: "gemini",
    status: "running",
    model_answers: [],
    verdict: null,
    decision_insurance: null,
    created_at: "2026-07-22T00:00:00Z",
    ...overrides,
  };
}

function sseResponse(event: string, data: unknown) {
  const body = `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
  return new Response(body, {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  });
}

test("local tombstone skips stream and fallback polling", async () => {
  let fetchCalls = 0;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => {
    fetchCalls += 1;
    return new Response("unexpected", { status: 500 });
  };
  try {
    const result = await streamTurn(auth, "turn-1", () => undefined, {
      isTurnDeleted: () => true,
    });

    assert.deepEqual(result, { reason: "turn_deleted" });
    assert.equal(fetchCalls, 0);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("turn_deleted SSE event is terminal and skips fallback polling", async () => {
  const events: string[] = [];
  let fetchCalls = 0;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => {
    fetchCalls += 1;
    return sseResponse("turn_deleted", { turn_id: "turn-1" });
  };
  try {
    const result = await streamTurn(auth, "turn-1", (event) => events.push(event));

    assert.deepEqual(result, { reason: "turn_deleted" });
    assert.deepEqual(events, ["turn_deleted"]);
    assert.equal(fetchCalls, 1);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("polling 404 after stream disconnect is terminal deletion", async () => {
  let fetchCalls = 0;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => {
    fetchCalls += 1;
    if (fetchCalls === 1) {
      return new Response("", { status: 503, statusText: "Service unavailable" });
    }
    return new Response(JSON.stringify({ error: "NOT_FOUND", message: "Turn not found" }), {
      status: 404,
      headers: { "content-type": "application/json" },
    });
  };
  try {
    const result = await streamTurn(auth, "turn-1", () => undefined);

    assert.deepEqual(result, { reason: "turn_deleted" });
    assert.equal(fetchCalls, 2);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("tombstone added while poll is pending ignores late poll response", async () => {
  const events: string[] = [];
  let fetchCalls = 0;
  let deleted = false;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => {
    fetchCalls += 1;
    if (fetchCalls === 1) {
      return new Response("", { status: 503, statusText: "Service unavailable" });
    }
    deleted = true;
    return new Response(JSON.stringify(turn()), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  };
  try {
    const result = await streamTurn(auth, "turn-1", (event) => events.push(event), {
      isTurnDeleted: () => deleted,
    });

    assert.deepEqual(result, { reason: "turn_deleted" });
    assert.deepEqual(events, []);
    assert.equal(fetchCalls, 2);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("non-404 polling errors are not classified as deletion", async () => {
  for (const status of [403, 429, 500]) {
    let fetchCalls = 0;
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async () => {
      fetchCalls += 1;
      if (fetchCalls === 1) {
        return new Response("", { status: 503, statusText: "Service unavailable" });
      }
      return new Response(JSON.stringify({ error: "ERROR", message: "Nope" }), {
        status,
        headers: { "content-type": "application/json" },
      });
    };
    try {
      await assert.rejects(() => streamTurn(auth, "turn-1", () => undefined));
    } finally {
      globalThis.fetch = originalFetch;
    }
  }
});

test("confirmed local deletion blocks resume", async () => {
  seedChatTurns("chat-resume", [turn({ id: "turn-resume", chat_id: "chat-resume" })]);
  removeTurn("chat-resume", "turn-resume");

  await resumeRunningTurns(auth, "chat-resume", [
    turn({ id: "turn-resume", chat_id: "chat-resume" }),
  ]);

  assert.deepEqual(getChatTurns("chat-resume"), []);
});
