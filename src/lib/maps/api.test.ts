import { describe, expect, it } from "vitest";

import { mapsCensusExportXlsxPath, parseExportFilename } from "@/lib/maps/api";

describe("mapsCensusExportXlsxPath", () => {
  it("targets the xlsx endpoint without a tier filter", () => {
    expect(mapsCensusExportXlsxPath("run-123")).toBe("/maps/runs/run-123/export.xlsx");
  });
});

describe("parseExportFilename", () => {
  it("reads the filename from a content-disposition header", () => {
    expect(
      parseExportFilename('attachment; filename="dz-maps-census-export.xlsx"', "fallback.xlsx"),
    ).toBe("dz-maps-census-export.xlsx");
  });

  it("falls back when the header is missing", () => {
    expect(parseExportFilename(null, "fallback.xlsx")).toBe("fallback.xlsx");
  });
});
