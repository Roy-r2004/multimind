import assert from "node:assert/strict";
import test from "node:test";

import { streamTurn } from "../../src/lib/api/stream.ts";
import {
  getChatTurns,
  removeTurn,
  resumeRunningTurns,
  runTurnInBackground,
  seedChatTurns,
  stopActiveTurn,
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

test("stopActiveTurn deletes the explicitly captured turn ID", async () => {
  const originalFetch = globalThis.fetch;
  const requests: Array<{ url: string; method: string }> = [];
  globalThis.fetch = async (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    requests.push({ url, method });
    if (method === "DELETE") {
      return new Response(JSON.stringify({ turn_id: "turn-exact", deleted: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }
    return new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () =>
        reject(new DOMException("Aborted", "AbortError")),
      );
    });
  };
  try {
    const pending = turn({ id: "turn-exact", chat_id: "chat-exact" });
    const running = runTurnInBackground(auth, "chat-exact", pending);

    await stopActiveTurn(auth, "chat-exact", "turn-exact");
    await running;

    assert.equal(
      requests.some(
        ({ url, method }) =>
          method === "DELETE" && url.endsWith("/chats/chat-exact/turns/turn-exact"),
      ),
      true,
    );
    assert.deepEqual(getChatTurns("chat-exact"), []);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("duplicate stops issue only one delete request", async () => {
  const originalFetch = globalThis.fetch;
  let deleteCalls = 0;
  globalThis.fetch = async (_input, init) => {
    if (init?.method === "DELETE") {
      deleteCalls += 1;
      await Promise.resolve();
      return new Response(JSON.stringify({ turn_id: "turn-duplicate", deleted: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }
    return new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () =>
        reject(new DOMException("Aborted", "AbortError")),
      );
    });
  };
  try {
    const pending = turn({ id: "turn-duplicate", chat_id: "chat-duplicate" });
    const running = runTurnInBackground(auth, "chat-duplicate", pending);

    await Promise.all([
      stopActiveTurn(auth, "chat-duplicate", "turn-duplicate"),
      stopActiveTurn(auth, "chat-duplicate", "turn-duplicate"),
    ]);
    await running;

    assert.equal(deleteCalls, 1);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
