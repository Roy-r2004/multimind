import type { ApiSimpleExplanation, ApiVerdict } from "@/lib/api/types";

export type SimpleExplanationSurface = "none" | "loading" | "ready";

export function visibleSimpleExplanationContent(
  verdict: ApiVerdict | null | undefined,
): string | null {
  const explanation: ApiSimpleExplanation | null | undefined = verdict?.simple_explanation;
  if (!explanation || explanation.status !== "succeeded") return null;
  const content = (explanation.content ?? "").trim();
  return content || null;
}

export function simpleExplanationSurface(
  verdict: ApiVerdict | null | undefined,
  options?: { pollActive?: boolean; pollTimedOut?: boolean },
): SimpleExplanationSurface {
  if (!verdict) return "none";
  if (visibleSimpleExplanationContent(verdict)) return "ready";
  const status = verdict.simple_explanation?.status;
  if (status === "failed") return "none";
  if (options?.pollTimedOut) return "none";
  if (status === "pending") return "loading";
  if (options?.pollActive) return "loading";
  return "none";
}
