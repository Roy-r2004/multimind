import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import {
  formatCompactDate,
  formatCost,
  formatTokensExact,
  formatTokensLabel,
  friendlyModelLabel,
  friendlyUsageActivity,
} from "../../src/lib/cost.ts";

const root = join(dirname(fileURLToPath(import.meta.url)), "../..");

test("formatCost handles ordinary and very small values", () => {
  assert.equal(formatCost(0), "$0.00");
  assert.equal(formatCost(14.82), "$14.82");
  assert.equal(formatCost(0.0042), "$0.0042");
  assert.equal(formatCost(0.0004), "$0.0004");
});

test("formatTokensExact and label", () => {
  assert.match(formatTokensExact(128450), /128,?450/);
  assert.match(formatTokensLabel(4230), /4,?230 tokens/);
});

test("friendlyUsageActivity maps operations to simple labels", () => {
  assert.equal(friendlyUsageActivity("council_answer", "answer"), "Chat answer");
  assert.equal(friendlyUsageActivity("verdict", "verdict"), "Verdict");
  assert.equal(friendlyUsageActivity("brain_learn", "brain"), "Brain");
  assert.equal(friendlyUsageActivity("lesson_build", "lesson"), "Lesson");
  assert.equal(friendlyUsageActivity("lesson_discuss", "lesson"), "Lesson");
  assert.equal(friendlyUsageActivity("brain_index", "embedding"), "Embedding");
  assert.equal(friendlyUsageActivity("facility_extract", "extraction"), "Scraping");
  assert.equal(friendlyUsageActivity("facility_cleanup", "scraping"), "Scraping");
  assert.equal(friendlyUsageActivity("discovery_plan", "scraping"), "Scraping");
  assert.equal(friendlyUsageActivity("blueprint_research", "blueprint"), "Blueprint");
  assert.equal(friendlyUsageActivity("team_planner", "planner"), "Planner");
  assert.equal(friendlyUsageActivity("maps_classify", "classification"), "Classification");
  assert.equal(friendlyUsageActivity("maps_website_recovery", "classification"), "Classification");
  assert.equal(friendlyUsageActivity("document_suggest", "document"), "Document");
  assert.equal(friendlyUsageActivity("prompt_improve", "helper"), "AI helper");
  assert.equal(friendlyUsageActivity("mystery_op", "other"), "Other");
});

test("friendlyModelLabel cleans slugs without exposing raw provider paths as primary", () => {
  assert.match(friendlyModelLabel("or:openai--gpt-5.5"), /GPT/i);
  assert.equal(friendlyModelLabel(""), "Unknown model");
});

test("formatCompactDate returns readable date", () => {
  assert.match(formatCompactDate("2026-08-01T12:00:00Z"), /2026/);
});

test("AppShell exposes Usage & Costs for normal users", () => {
  const src = readFileSync(join(root, "src/components/AppShell.tsx"), "utf8");
  assert.match(src, /to: "\/usage"/);
  assert.match(src, /Usage & Costs/);
});

test("simplified usage page emphasizes Total spent without complex UI", () => {
  const src = readFileSync(join(root, "src/routes/usage.tsx"), "utf8");
  assert.match(src, /Total spent/);
  assert.match(src, /all_time_usd/);
  assert.match(src, /Today/);
  assert.match(src, /This month/);
  assert.match(src, /Total tokens/);
  assert.match(src, /Your personal AI usage and spending/);
  assert.match(src, /Your total includes historical AI usage previously tracked/);
  assert.match(src, /friendlyUsageActivity/);
  assert.match(src, /Recent activity/);
  assert.match(src, /Load more/);
  assert.match(src, /data-testid="total-spent-card"/);

  // Removed clutter from the user page
  assert.doesNotMatch(src, /timeseries/);
  assert.doesNotMatch(src, /breakdown/);
  assert.doesNotMatch(src, /BarChart/);
  assert.doesNotMatch(src, /statusFilter/);
  assert.doesNotMatch(src, /By model/);
  assert.doesNotMatch(src, /Tracked calls/);
  assert.doesNotMatch(src, /This week/);
  assert.doesNotMatch(src, /min-w-\[640px\]/);
  assert.doesNotMatch(src, /by_user/);
  assert.doesNotMatch(src, /request_id/);
  assert.doesNotMatch(src, /row\.operation \|\| row\.kind/);
});

test("Admin usage label remains Usage & Costs and org-scoped route", () => {
  const adminShell = readFileSync(join(root, "src/components/admin/AdminShell.tsx"), "utf8");
  const adminUsage = readFileSync(join(root, "src/routes/admin/usage.tsx"), "utf8");
  assert.match(adminShell, /Usage & Costs/);
  assert.match(adminShell, /\/admin\/usage/);
  assert.doesNotMatch(adminShell, /Usage & Billing/);
  assert.match(adminUsage, /api\.admin\.usage/);
  assert.match(adminUsage, /AdminPageFrame/);
});

test("api.usage client methods exist", () => {
  const src = readFileSync(join(root, "src/lib/api/index.ts"), "utf8");
  assert.match(src, /usage:\s*\{/);
  assert.match(src, /\/usage\/summary/);
  assert.match(src, /\/usage\/records/);
});
