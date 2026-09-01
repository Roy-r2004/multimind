import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "../..");
const messageContentSrc = readFileSync(
  join(root, "src/components/chat/MessageContent.tsx"),
  "utf8",
);
const chatSrc = readFileSync(join(root, "src/routes/chat.tsx"), "utf8");

test("Verdict opts into stronger long-form hierarchy while Council keeps the default", () => {
  assert.match(messageContentSrc, /variant\?: "default" \| "verdict"/);
  assert.match(messageContentSrc, /variant = "default"/);
  assert.match(messageContentSrc, /text-xl font-semibold[^\n]+sm:text-2xl/);
  assert.match(messageContentSrc, /text-lg font-semibold[^\n]+sm:text-xl/);
  assert.match(messageContentSrc, /text-base font-semibold[^\n]+sm:text-lg/);
  assert.match(messageContentSrc, /: "mb-2 mt-4 font-semibold"/);

  assert.match(chatSrc, /<MessageContent variant="verdict">\{turn\.verdict\.text\}/);
  assert.match(chatSrc, /<MessageContent>\{a\?\.text \?\? ""\}<\/MessageContent>/);
});

test("shared tables scroll, resist crushing, and keep long cell content safe", () => {
  assert.match(messageContentSrc, /overflow-x-auto rounded-lg border/);
  assert.match(messageContentSrc, /min-w-\[36rem\] table-auto/);
  assert.match(messageContentSrc, /\[overflow-wrap:break-word\]/);
  assert.match(messageContentSrc, /\[&_a\]:break-all/);
  assert.match(messageContentSrc, /\[&_code\]:break-all/);
  assert.match(messageContentSrc, /isVerdict \? "mb-5 mt-1" : "mb-3"/);
});

test("the existing Markdown parser and long-answer behavior stay in place", () => {
  assert.match(messageContentSrc, /remarkPlugins=\{\[remarkGfm, remarkBreaks\]\}/);
  assert.doesNotMatch(chatSrc, /max-h[^\n]+turn\.verdict\.text/);
});
