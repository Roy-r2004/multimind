import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { MapsRunStatusBadge } from "./MapsRunStatusBadge.tsx";

function renderBadge(status: string): string {
  return renderToStaticMarkup(createElement(MapsRunStatusBadge, { status }));
}

function renderedLabel(status: string): string {
  const html = renderBadge(status);
  const match = html.match(/>([^<]*)</);
  assert.ok(match, `expected badge text in ${html}`);
  return match[1];
}

test("completed_with_warnings renders completed, not the warnings wording", () => {
  const html = renderBadge("completed_with_warnings");
  const match = html.match(/>([^<]*)</);
  assert.ok(match, `expected badge text in ${html}`);
  assert.equal(match[1], "completed");
  assert.notEqual(match[1], "completed w/ warnings");
  assert.equal(html.includes("completed w/ warnings"), false);
});

test("completed still renders completed", () => {
  assert.equal(renderedLabel("completed"), "completed");
});

test("failed still renders failed", () => {
  assert.equal(renderedLabel("failed"), "failed");
});
