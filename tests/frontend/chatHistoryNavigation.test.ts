import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

/**
 * Root cause of "See all" silently doing nothing: TanStack Router's file-based
 * routing auto-nests a dotted filename under its prefix's route file — so
 * `chat.history.tsx` was parented under `chat.tsx`'s route. `chat.tsx` renders
 * the full Chat Council page with no <Outlet/>, so the nested child route
 * never actually mounted: the URL changed, but the visible page never did.
 * The fix renames the file to `chat_.history.tsx` (TanStack Router's
 * trailing-underscore convention for "don't nest this under the matching
 * parent"), which keeps the URL at /chat/history but parents it under root
 * instead. These tests pin that fix down structurally.
 */

const routesDir = path.join(import.meta.dirname, "..", "..", "src", "routes");
const routeTreePath = path.join(import.meta.dirname, "..", "..", "src", "routeTree.gen.ts");

test("chat.tsx still renders no Outlet (if that ever changes, the non-nesting workaround may no longer be needed)", () => {
  const chatSource = readFileSync(path.join(routesDir, "chat.tsx"), "utf8");
  assert.ok(!chatSource.includes("<Outlet"));
});

test("the See All route file uses the non-nesting filename, not the auto-nesting one", () => {
  const files = readdirSync(routesDir);
  assert.ok(
    files.includes("chat_.history.tsx"),
    "expected src/routes/chat_.history.tsx so TanStack Router treats /chat/history as a sibling of /chat, not a child",
  );
  assert.ok(
    !files.includes("chat.history.tsx"),
    "chat.history.tsx must not exist: it would auto-nest under chat.tsx's route, which never renders an Outlet, " +
      "so the nested child would never mount even though the URL changes",
  );
});

test("the generated route tree parents /chat/history directly under root, not under the /chat route", () => {
  const treeSource = readFileSync(routeTreePath, "utf8");
  const match = treeSource.match(
    /const ChatHistoryRoute = ChatHistoryRouteImport\.update\(\{[\s\S]*?\}\s*as any\)/,
  );
  assert.ok(match, "could not find ChatHistoryRoute registration in routeTree.gen.ts");
  assert.ok(match![0].includes("path: '/chat/history'"), "URL path must stay /chat/history");
  assert.ok(
    match![0].includes("getParentRoute: () => rootRouteImport"),
    "ChatHistoryRoute must be parented directly under root, not under ChatRoute (whose component renders no Outlet)",
  );
});
