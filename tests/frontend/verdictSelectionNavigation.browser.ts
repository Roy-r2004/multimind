/** Bundle with rolldown and run in a real browser; results appear in #results. */
import {
  captureSelectionLocator,
  locateSelectionRange,
  scrollToSelectionRange,
  highlightSelectionRange,
} from "../../src/lib/verdictSelectionNavigation";

async function run() {
  let checks = 0;
  const check = (condition: unknown, message: string) => {
    if (!condition) throw new Error(message);
    checks++;
  };
  document.body.innerHTML = `<div id="thread" style="height:200px;overflow:auto">
    <div style="height:600px"></div><div data-verdict-copy-root>
    <p>Same sentence.</p><p>Lead <strong>Same sentence.</strong> tail</p>
    <div data-verdict-table-copy-control>Copy table</div>
    <ul><li>First bullet</li><li>Second <em>bullet</em></li></ul>
    </div><div style="height:600px"></div></div><pre id="results"></pre>`;
  const root = document.querySelector("[data-verdict-copy-root]")!;
  const thread = document.getElementById("thread")!;
  const sentence = root.querySelector("strong")!.firstChild!;
  const range = document.createRange();
  range.setStart(sentence, 0);
  range.setEnd(sentence, sentence.textContent!.length);
  const locator = captureSelectionLocator(root, range)!;
  const pin = JSON.parse(
    JSON.stringify({ selectedText: "Same sentence.", selectionLocator: locator }),
  );
  let found = locateSelectionRange(root, pin)!;
  check(
    found.startContainer === sentence && found.startOffset === 0,
    "duplicate occurrence must be exact",
  );
  const first = document.createRange();
  first.selectNodeContents(root.querySelector("p")!);
  const firstPin = {
    selectedText: first.toString(),
    selectionLocator: captureSelectionLocator(root, first),
  };
  check(
    locateSelectionRange(root, firstPin)!.startContainer !== found.startContainer,
    "two pins must target independently",
  );
  check(
    locateSelectionRange(root, { selectedText: "Same sentence." }) === null,
    "ambiguous legacy pins must not guess",
  );
  const bullets = document.createRange();
  bullets.setStart(root.querySelectorAll("li")[0].firstChild!, 2);
  bullets.setEnd(root.querySelector("em")!.firstChild!, 4);
  const bulletPin = {
    selectedText: "• rst bullet\n• Second bull",
    selectionLocator: captureSelectionLocator(root, bullets),
  };
  check(
    locateSelectionRange(root, bulletPin)!.toString() === bullets.toString(),
    "cross-node range excludes Copy controls from offsets",
  );
  const legacy = locateSelectionRange(root, {
    selectedText: "• First bullet",
    selectedHtml: "<ul><li>First bullet</li></ul>",
  });
  check(legacy?.toString() === "First bullet", "legacy list pins use inert HTML text fallback");
  // Re-render like a refresh, so stored locators cannot rely on node identities.
  root.innerHTML = root.innerHTML;
  found = locateSelectionRange(root, pin)!;
  check(
    found.startContainer.parentElement?.tagName === "STRONG",
    "refresh must retain exact occurrence",
  );
  scrollToSelectionRange(found, thread);
  const rect = found.getBoundingClientRect();
  const bounds = thread.getBoundingClientRect();
  check(
    rect.top >= bounds.top && rect.bottom <= bounds.bottom,
    "fragment must be visible after scroll",
  );
  const selection = window.getSelection()!;
  selection.removeAllRanges();
  selection.addRange(found);
  const html = root.innerHTML;
  const clear = highlightSelectionRange(found, thread);
  check(
    document.querySelectorAll("[data-selection-pin-highlight] > div").length > 0,
    "fragment highlight must be painted",
  );
  check(
    root.innerHTML === html && selection.toString() === "Same sentence.",
    "highlight must not change content or selection",
  );
  await new Promise((resolve) => setTimeout(resolve, 1700));
  check(
    !document.querySelector("[data-selection-pin-highlight]"),
    "highlight must clear automatically",
  );
  clear();
  document.getElementById("results")!.textContent = `PASS: ${checks} browser checks`;
}
run().catch((error) => {
  document.getElementById("results")!.textContent = `FAIL: ${error.message}`;
});
