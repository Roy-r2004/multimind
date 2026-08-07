import assert from "node:assert/strict";
import test from "node:test";

import { shouldApplyRefreshResult } from "../../src/lib/chatStoreRefresh.ts";

test("newer refresh generation wins; older response is ignored", () => {
  const authA = { token: "tok-a", orgId: "org-a" };
  const authB = { token: "tok-b", orgId: "org-b" };

  // A starts (gen 1), B starts (gen 2), B would apply
  assert.equal(
    shouldApplyRefreshResult({
      requestGeneration: 2,
      currentGeneration: 2,
      requestAuth: authB,
      currentAuth: authB,
    }),
    true,
  );

  // A resolves late — generation stale
  assert.equal(
    shouldApplyRefreshResult({
      requestGeneration: 1,
      currentGeneration: 2,
      requestAuth: authA,
      currentAuth: authB,
    }),
    false,
  );
});

test("logout (null current auth) rejects in-flight refresh apply", () => {
  assert.equal(
    shouldApplyRefreshResult({
      requestGeneration: 3,
      currentGeneration: 3,
      requestAuth: { token: "tok", orgId: "org" },
      currentAuth: null,
    }),
    false,
  );
});

test("auth/org mismatch rejects apply even if generation matches", () => {
  assert.equal(
    shouldApplyRefreshResult({
      requestGeneration: 5,
      currentGeneration: 5,
      requestAuth: { token: "tok-old", orgId: "org-1" },
      currentAuth: { token: "tok-new", orgId: "org-1" },
    }),
    false,
  );
  assert.equal(
    shouldApplyRefreshResult({
      requestGeneration: 5,
      currentGeneration: 5,
      requestAuth: { token: "tok", orgId: "org-1" },
      currentAuth: { token: "tok", orgId: "org-2" },
    }),
    false,
  );
});

test("matching generation and auth allows apply", () => {
  const auth = { token: "tok", orgId: "org" };
  assert.equal(
    shouldApplyRefreshResult({
      requestGeneration: 9,
      currentGeneration: 9,
      requestAuth: auth,
      currentAuth: auth,
    }),
    true,
  );
});
