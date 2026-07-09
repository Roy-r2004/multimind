/** User-defined criteria that calibrate council confidence scores and verdict judgment. */

export const ASSESSMENT_CRITERIA_MARKER = "## Assessment criteria";

export const DEFAULT_COMPANY_ASSESSMENT_CRITERIA = `1. Financial health, margins, and runway
2. Competitive position and defensible moat
3. Growth trajectory and addressable market
4. Risk factors, regulation, and downside scenarios
5. Management quality and execution track record`;

export function formatAssessmentBlock(criteria: string): string {
  const body = criteria.trim();
  if (!body) return "";
  return `${ASSESSMENT_CRITERIA_MARKER}\nCalibrate every CONFIDENCE score (0–100) against these priorities:\n${body}`;
}

export function extractAssessmentCriteria(customInstructions?: string | null): string {
  if (!customInstructions?.trim()) return "";
  const marker = customInstructions.indexOf(ASSESSMENT_CRITERIA_MARKER);
  if (marker === -1) return "";
  const after = customInstructions.slice(marker + ASSESSMENT_CRITERIA_MARKER.length).trim();
  const nextSection = after.search(/\n## /);
  const block = nextSection === -1 ? after : after.slice(0, nextSection);
  return block.replace(/^Calibrate every CONFIDENCE score \(0–100\) against these priorities:\s*/i, "").trim();
}

export function stripAssessmentBlock(customInstructions?: string | null): string {
  if (!customInstructions?.trim()) return "";
  const marker = customInstructions.indexOf(ASSESSMENT_CRITERIA_MARKER);
  if (marker === -1) return customInstructions.trim();
  const before = customInstructions.slice(0, marker).trim();
  const afterMarker = customInstructions.slice(marker + ASSESSMENT_CRITERIA_MARKER.length);
  const nextSection = afterMarker.search(/\n## /);
  const rest = nextSection === -1 ? "" : afterMarker.slice(nextSection + 1).trim();
  return [before, rest].filter(Boolean).join("\n\n").trim();
}

export function mergeAssessmentIntoInstructions(
  base: string | undefined,
  criteria: string,
): string | undefined {
  const withoutCriteria = stripAssessmentBlock(base);
  const criteriaBlock = formatAssessmentBlock(criteria);
  const chunks = [withoutCriteria, criteriaBlock].filter((s) => s.trim());
  return chunks.length ? chunks.join("\n\n") : undefined;
}

export function parseCriteriaLines(criteria: string): string[] {
  return criteria
    .split("\n")
    .map((line) => line.replace(/^\d+\.\s*/, "").trim())
    .filter(Boolean);
}
