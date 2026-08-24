import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { attachmentTypeLabel, sentTurnAttachmentItems } from "../../src/lib/sentTurnAttachments.ts";

test("question without persisted attachments has no attachment display items", () => {
  assert.deepEqual(sentTurnAttachmentItems(undefined), []);
  assert.deepEqual(sentTurnAttachmentItems([]), []);
});

test("one persisted attachment exposes its filename and file type", () => {
  const items = sentTurnAttachmentItems([
    { id: "att-1", filename: "annual-report.pdf", content_type: "application/pdf" },
  ]);
  assert.equal(items.length, 1);
  assert.equal(items[0]?.filename, "annual-report.pdf");
  assert.equal(items[0]?.typeLabel, "PDF");
});

test("multiple persisted attachments are all retained in turn order", () => {
  const items = sentTurnAttachmentItems([
    { id: "att-1", filename: "report.pdf", content_type: "application/pdf" },
    {
      id: "att-2",
      filename: "numbers.xlsx",
      content_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    },
  ]);
  assert.deepEqual(
    items.map((item) => [item.filename, item.typeLabel]),
    [
      ["report.pdf", "PDF"],
      ["numbers.xlsx", "XLSX"],
    ],
  );
});

test("long filenames keep their full persisted value and the component truncates visually", () => {
  const filename = `${"very-long-financial-report-".repeat(8)}2026.docx`;
  assert.equal(
    sentTurnAttachmentItems([
      { id: "att-long", filename, content_type: "application/octet-stream" },
    ])[0]?.filename,
    filename,
  );
  assert.equal(attachmentTypeLabel(filename), "DOCX");

  const source = readFileSync(
    new URL("../../src/components/chat/SentTurnAttachments.tsx", import.meta.url),
    "utf8",
  );
  assert.match(source, /min-w-0 truncate/);
  assert.match(source, /title=\{attachment\.filename\}/);
});

test("display model consumes persisted turn attachments and supports webm", () => {
  const persistedTurn = {
    attachments: [{ id: "att-server", filename: "recording.webm", content_type: "audio/webm" }],
  };
  const items = sentTurnAttachmentItems(persistedTurn.attachments);
  assert.deepEqual(
    items.map((item) => [item.id, item.filename, item.typeLabel]),
    [["att-server", "recording.webm", "WEBM"]],
  );
});
