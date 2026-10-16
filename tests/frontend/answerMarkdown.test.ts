import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { presentAnswerMarkdown } from "../../src/lib/answerMarkdown.ts";

const root = join(dirname(fileURLToPath(import.meta.url)), "../..");
const messageContentSrc = readFileSync(
  join(root, "src/components/chat/MessageContent.tsx"),
  "utf8",
);
const chatSrc = readFileSync(join(root, "src/routes/chat.tsx"), "utf8");
const stylesSrc = readFileSync(join(root, "src/styles.css"), "utf8");
const copySrc = readFileSync(join(root, "src/lib/singleAiAnswerActions.ts"), "utf8");
const promptSrc = readFileSync(join(root, "backend/app/prompts/system/base.j2"), "utf8");

/** Rehabilitation assessment: five columns, two body rows wrapped onto extra lines. */
const rehabilitation = `
## Rehabilitation protocol assessment

The current plan leaves several functional gaps. Each domain is scored against the discharge goal.

| Domain | Current state | Gap | Recommendation | Priority |
| --- | --- | --- | --- | --- |
| Mobility | Ambulates 50m with supervision | Endurance drops after 30m | Continue gait training three times weekly | High |
| ADLs | Requires
setup help for bathing and dressing throughout the morning routine | Safety risk when unsupervised | **Add** a morning ADL block [p. 12] | Medium |
| Strength | 4/5 hip flexors | Below the discharge goal | Progress closed-chain resistance | Low |
| Balance | Uses a cane indoors | Cannot pass the tandem stance test | Add a daily balance circuit | High |
| Endurance | Six-minute walk is
below the target distance
recorded at admission | Limited community access | Interval walking with rest breaks | Medium |

### Notes

- Keep the morning session supervised.
- Reassess on day 14.

A ratio such as 1 | 2 in prose is not a table.

See the [protocol](https://example.com/protocol) for the full plan.

| Measure | Today | Goal |
| --- | --- | --- |
| Pain | 6/10 | 2/10 |
| ROM | [guide](https://example.com/guide) | 110° |
`.trim();

const joinedAdls =
  "| ADLs | Requires setup help for bathing and dressing throughout the morning routine | Safety risk when unsupervised | **Add** a morning ADL block [p. 12] | Medium |";
const joinedEndurance =
  "| Endurance | Six-minute walk is below the target distance recorded at admission | Limited community access | Interval walking with rest breaks | Medium |";

function words(value: string): string[] {
  return value.split(/\s+/).filter(Boolean);
}

function cells(row: string): string[] {
  const trimmed = row.trim();
  let parts = trimmed.split("|");
  if (trimmed.startsWith("|")) parts = parts.slice(1);
  if (trimmed.endsWith("|")) parts = parts.slice(0, -1);
  return parts.map((part) => part.trim());
}

test("valid five-column tables stay one row per line", () => {
  const source = [
    "| Domain | Current state | Gap | Recommendation | Priority |",
    "| --- | --- | --- | --- | --- |",
    "| Mobility | Ambulates 50m with supervision | Endurance drops after 30m | Continue gait training | High |",
    "| Balance | Uses a cane indoors | Cannot pass tandem stance | Add a daily circuit | High |",
  ].join("\n");
  assert.equal(presentAnswerMarkdown(source), source);
  for (const row of source.split("\n").slice(2)) {
    assert.equal(cells(row).length, 5);
  }
});

test("long paragraphs inside wrapped cells become one five-column row", () => {
  const presented = presentAnswerMarkdown(rehabilitation);
  assert.ok(presented.includes(joinedAdls));
  assert.ok(presented.includes(joinedEndurance));
  assert.equal(cells(joinedAdls).length, 5);
  assert.equal(cells(joinedEndurance).length, 5);
  assert.match(
    cells(joinedAdls)[1],
    /Requires setup help for bathing and dressing throughout the morning routine/,
  );
});

test("a blank line after the header still keeps unambiguous body rows in the table", () => {
  const source = [
    "| Domain | Current state | Gap | Recommendation | Priority |",
    "| --- | --- | --- | --- | --- |",
    "",
    "| Mobility | Ambulates 50m with supervision | Endurance drops after 30m | Continue gait training three times weekly | High |",
    "| ADLs | Requires",
    "setup help for bathing and dressing throughout the morning routine | Safety risk when unsupervised | **Add** a morning ADL block [p. 12] | Medium |",
    "",
    "| Balance | Uses a cane indoors | Cannot pass the tandem stance test | Add a daily balance circuit | High |",
    "",
    "A ratio such as 1 | 2 in prose is not a table.",
    "",
    "| Not | pulled | into | the | table |",
  ].join("\n");
  const presented = presentAnswerMarkdown(source);
  const lines = presented.split("\n");
  assert.deepEqual(lines.slice(0, 4), [
    "| Domain | Current state | Gap | Recommendation | Priority |",
    "| --- | --- | --- | --- | --- |",
    "| Mobility | Ambulates 50m with supervision | Endurance drops after 30m | Continue gait training three times weekly | High |",
    "| ADLs | Requires setup help for bathing and dressing throughout the morning routine | Safety risk when unsupervised | **Add** a morning ADL block [p. 12] | Medium |",
  ]);
  assert.equal(
    lines[4],
    "| Balance | Uses a cane indoors | Cannot pass the tandem stance test | Add a daily balance circuit | High |",
  );
  assert.ok(presented.includes("A ratio such as 1 | 2 in prose is not a table."));
  assert.ok(presented.includes("| Not | pulled | into | the | table |"));
  assert.ok(presented.indexOf("1 | 2") < presented.indexOf("| Not | pulled"));
});

test("a later table separated by a blank line stays a separate table", () => {
  const source = [
    "| Domain | Current state | Gap | Recommendation | Priority |",
    "| --- | --- | --- | --- | --- |",
    "| Mobility | walks | endurance | gait | High |",
    "",
    "| Domain | Current state | Gap | Recommendation | Priority |",
    "| --- | --- | --- | --- | --- |",
    "| ADLs | bathing | safety | morning block | Medium |",
  ].join("\n");
  assert.equal(presentAnswerMarkdown(source), source);
});

test("a split row is joined only when the remainder has the missing pipes", () => {
  const source = [
    "| Domain | Current state | Gap | Recommendation | Priority |",
    "| --- | --- | --- | --- | --- |",
    "| ADLs | Requires",
    "setup help for bathing | Safety risk | **Add** therapy | Medium |",
  ].join("\n");
  const presented = presentAnswerMarkdown(source);
  assert.equal(
    presented.split("\n")[2],
    "| ADLs | Requires setup help for bathing | Safety risk | **Add** therapy | Medium |",
  );
  assert.equal(cells(presented.split("\n")[2]).length, 5);
});

test("stray pipe separators outside a table stay ordinary text", () => {
  const presented = presentAnswerMarkdown(rehabilitation);
  assert.ok(presented.includes("A ratio such as 1 | 2 in prose is not a table."));
  const prose = "Use a | between values when you mean a ratio.\n";
  assert.equal(presentAnswerMarkdown(prose), prose);
});

test("multiple tables in one response are repaired independently", () => {
  const presented = presentAnswerMarkdown(rehabilitation);
  assert.equal(presented.split("\n").filter((line) => line.includes("---")).length, 2);
  assert.ok(presented.includes("| Pain | 6/10 | 2/10 |"));
  assert.ok(presented.includes("| ROM | [guide](https://example.com/guide) | 110° |"));
  assert.ok(presented.includes(joinedAdls));
});

test("tables keep the surrounding headings, lists, and paragraphs", () => {
  const presented = presentAnswerMarkdown(rehabilitation);
  assert.ok(presented.includes("## Rehabilitation protocol assessment"));
  assert.ok(presented.includes("### Notes"));
  assert.ok(presented.includes("- Keep the morning session supervised."));
  assert.ok(presented.includes("- Reassess on day 14."));
  assert.ok(
    presented.includes(
      "The current plan leaves several functional gaps. Each domain is scored against the discharge goal.",
    ),
  );
  assert.ok(presented.includes("See the [protocol](https://example.com/protocol) for the full plan."));
});

test("bold, links, and citations inside cells survive repair", () => {
  const presented = presentAnswerMarkdown(rehabilitation);
  assert.ok(presented.includes("**Add** a morning ADL block [p. 12]"));
  assert.ok(presented.includes("[guide](https://example.com/guide)"));
  assert.ok(presented.includes("[protocol](https://example.com/protocol)"));
});

test("code blocks keep literal pipe characters", () => {
  const source = [
    "Before the sample.",
    "",
    "```",
    "| Domain | Current state | Gap | Recommendation | Priority |",
    "| --- | --- | --- | --- | --- |",
    "| Keep | these | pipes | literal | please |",
    "```",
    "",
    "After the sample.",
    "",
    "| Domain | Current state | Gap | Recommendation | Priority |",
    "| --- | --- | --- | --- | --- |",
    "| Real | table | stays | a | table |",
  ].join("\n");
  assert.equal(presentAnswerMarkdown(source), source);

  const inline = [
    "| Topic | Example | Notes | Extra | Status |",
    "| --- | --- | --- | --- | --- |",
    "| Code | Use `a|b` in a cell | Keep it | Literal | Done |",
  ].join("\n");
  assert.equal(presentAnswerMarkdown(inline), inline);
});

test("streaming partial markdown stays intact and the completed text repairs", () => {
  const cut = rehabilitation.indexOf("| ADLs | Requires");
  const partial = rehabilitation.slice(0, cut + "| ADLs | Requires".length);
  const partialView = presentAnswerMarkdown(partial);
  const finalView = presentAnswerMarkdown(rehabilitation);

  assert.ok(partialView.includes("| Domain | Current state | Gap | Recommendation | Priority |"));
  assert.ok(partialView.includes("| ADLs | Requires"));
  assert.equal(partialView.includes("setup help for bathing"), false);
  for (const token of words(partial)) {
    assert.ok(words(partialView).includes(token), token);
  }

  assert.ok(finalView.includes(joinedAdls));
  assert.notEqual(partialView, finalView);
  assert.equal(presentAnswerMarkdown(finalView), finalView);
});

test("a saved answer repairs again from the original stored text", () => {
  const stored = rehabilitation;
  const firstVisit = presentAnswerMarkdown(stored);
  const afterRefresh = presentAnswerMarkdown(stored);
  assert.equal(afterRefresh, firstVisit);
  assert.ok(stored.includes("| ADLs | Requires\nsetup help"));
  assert.equal(stored.includes(joinedAdls), false);
  assert.ok(afterRefresh.includes(joinedAdls));
});

test("wide tables stay intact for horizontal scrolling", () => {
  const header = Array.from({ length: 12 }, (_, index) => `Column ${index + 1}`).join(" | ");
  const delimiter = Array.from({ length: 12 }, () => "---").join(" | ");
  const body = Array.from(
    { length: 12 },
    (_, index) => `A long comparison value for column ${index + 1}`,
  ).join(" | ");
  const source = `| ${header} |\n| ${delimiter} |\n| ${body} |`;
  assert.equal(presentAnswerMarkdown(source), source);
  assert.equal(cells(source.split("\n")[2]).length, 12);
  assert.match(messageContentSrc, /min-w-0 max-w-full overflow-x-auto rounded-lg border/);
  assert.match(messageContentSrc, /min-w-\[36rem\] table-auto/);
  assert.match(stylesSrc, /\.message-content \{\s*max-width: 100%;\s*min-width: 0;\s*\}/);
});

test("every original text segment is preserved", () => {
  const stored = rehabilitation;
  const presented = presentAnswerMarkdown(stored);
  assert.equal(stored, rehabilitation);
  for (const token of words(stored)) {
    assert.ok(words(presented).includes(token), token);
  }
});

test("an incomplete row is left unchanged instead of inventing cells", () => {
  const source = [
    "| Domain | Current state | Gap | Recommendation | Priority |",
    "| --- | --- | --- | --- | --- |",
    "| Mobility | Ambulates with supervision",
    "and still needs help",
  ].join("\n");
  const presented = presentAnswerMarkdown(source);
  assert.ok(presented.includes("| Mobility | Ambulates with supervision"));
  assert.ok(presented.includes("and still needs help"));
  assert.equal(presented.includes("| Mobility | Ambulates with supervision and still needs help |"), false);
  assert.equal(cells("| Mobility | Ambulates with supervision").length, 2);
});

test("too many pipes are not shifted into neighboring columns", () => {
  const source = [
    "| Domain | Current state | Gap | Recommendation | Priority |",
    "| --- | --- | --- | --- | --- |",
    "| Mobility | only three cells",
    "This paragraph mentions a pipe | but it would create the wrong columns | with extra | pipes | here | and | more |",
    "| Good | Complete | Row | Stays | Readable |",
  ].join("\n");
  const presented = presentAnswerMarkdown(source);
  assert.equal(presented, source);
  assert.ok(presented.includes("only three cells"));
  assert.ok(presented.includes("wrong columns"));
  assert.ok(presented.includes("| Good | Complete | Row | Stays | Readable |"));
});

test("ordinary answers and carriage returns stay stable", () => {
  const ordinary = "The answer is 42.\n\n- one\n- two\n\nA paragraph with a single line break\nstill has that break.\n";
  assert.equal(presentAnswerMarkdown(ordinary), ordinary);
  const crlf = "| A | B | C |\r\n| --- | --- | --- |\r\n| 1 | 2 | 3 |\r\n";
  assert.equal(presentAnswerMarkdown(crlf), "| A | B | C |\n| --- | --- | --- |\n| 1 | 2 | 3 |\n");
});

test("Copy, Pin, Verdict, and Council still render the stored answer", () => {
  assert.match(messageContentSrc, /presentAnswerMarkdown\(cleaned\)/);
  assert.match(messageContentSrc, /remarkPlugins=\{\[remarkGfm, remarkBreaks\]\}/);
  assert.match(chatSrc, /<MessageContent>\{a\?\.text \?\? ""\}<\/MessageContent>/);
  assert.match(chatSrc, /<MessageContent variant="verdict">\{turn\.verdict\.text\}<\/MessageContent>/);
  assert.match(chatSrc, /text=\{singleAi\.copyText\}/);
  assert.match(chatSrc, /text=\{turn\.verdict\.text\}/);
  assert.match(chatSrc, /data-testid="single-ai-pin"/);
  assert.match(chatSrc, /aria-label=\{isPinned \? "Unpin verdict" : "Pin verdict"\}/);
  assert.match(copySrc, /copyText: oneCompletedAnswer \? \(input\.text \?\? ""\) : ""/);
  assert.equal(chatSrc.includes("presentAnswerMarkdown"), false);
  assert.match(promptSrc, /put each row on a single line/);
});
