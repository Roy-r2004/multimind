import { apiRequest, getApiBase } from "@/lib/api/client";
import type {
  MapsCampaignActionResponse,
  MapsCensusCellItem,
  MapsCensusRunCreateInput,
  MapsCensusRunDetail,
  MapsCensusRunSummary,
  MapsPlaceItem,
  MapsPlaceListResponse,
  MapsRunLiveStats,
} from "@/lib/maps/types";

type Auth = { token: string; orgId: string };

export function createMapsCensusRun(auth: Auth, data: MapsCensusRunCreateInput) {
  return apiRequest<MapsCensusRunDetail>("/maps/runs", {
    method: "POST",
    body: data,
    token: auth.token,
    orgId: auth.orgId,
  });
}

export function listMapsCensusRuns(auth: Auth) {
  return apiRequest<MapsCensusRunSummary[]>("/maps/runs", {
    token: auth.token,
    orgId: auth.orgId,
  });
}

export function getMapsCensusRun(auth: Auth, runId: string) {
  return apiRequest<MapsCensusRunDetail>(`/maps/runs/${runId}`, {
    token: auth.token,
    orgId: auth.orgId,
  });
}

export function getMapsCensusRunLiveStats(auth: Auth, runId: string) {
  return apiRequest<MapsRunLiveStats>(`/maps/runs/${runId}/live-stats`, {
    token: auth.token,
    orgId: auth.orgId,
  });
}

export function listMapsCensusCells(auth: Auth, runId: string) {
  return apiRequest<MapsCensusCellItem[]>(`/maps/runs/${runId}/cells`, {
    token: auth.token,
    orgId: auth.orgId,
  });
}

export function listMapsCensusPlaces(
  auth: Auth,
  runId: string,
  filters: {
    relevantOnly?: boolean;
    withWebsiteOnly?: boolean;
    keepDropDecision?: "keep" | "drop";
    includeRemoved?: boolean;
    limit?: number;
    offset?: number;
  } = {},
) {
  const params = new URLSearchParams();
  if (filters.relevantOnly) params.set("relevant_only", "true");
  if (filters.withWebsiteOnly) params.set("with_website_only", "true");
  if (filters.keepDropDecision) params.set("keep_drop_decision", filters.keepDropDecision);
  if (filters.includeRemoved) params.set("include_removed", "true");
  if (filters.limit !== undefined) params.set("limit", String(filters.limit));
  if (filters.offset !== undefined) params.set("offset", String(filters.offset));
  const query = params.toString();
  return apiRequest<MapsPlaceListResponse>(`/maps/runs/${runId}/places${query ? `?${query}` : ""}`, {
    token: auth.token,
    orgId: auth.orgId,
  });
}

/** Phase 1 "remove row" — hides a place from the Phase 1 view and, if it was
 * already keep/drop-confirmed "keep", drops it out of Phase 2 immediately. */
export function excludeMapsCensusPlace(
  auth: Auth,
  runId: string,
  placeId: string,
  reason?: string,
) {
  const params = new URLSearchParams();
  if (reason) params.set("reason", reason);
  const query = params.toString();
  return apiRequest<MapsPlaceItem>(
    `/maps/runs/${runId}/places/${placeId}/exclude${query ? `?${query}` : ""}`,
    {
      method: "POST",
      token: auth.token,
      orgId: auth.orgId,
    },
  );
}

/** "Proceed to Phase 2" — triggers the strict keep/drop gate over the current
 * (post-manual-removal) Phase 1 set. */
export function advanceMapsCensusToPhase2(auth: Auth, runId: string) {
  return apiRequest<MapsCampaignActionResponse>(`/maps/runs/${runId}/advance-to-phase-2`, {
    method: "POST",
    token: auth.token,
    orgId: auth.orgId,
  });
}

export function deleteMapsCensusRun(auth: Auth, runId: string) {
  return apiRequest<void>(`/maps/runs/${runId}`, {
    method: "DELETE",
    token: auth.token,
    orgId: auth.orgId,
  });
}

export function refreshMapsCensusWebsites(auth: Auth, runId: string) {
  return apiRequest<MapsCensusRunDetail>(`/maps/runs/${runId}/refresh-websites`, {
    method: "POST",
    token: auth.token,
    orgId: auth.orgId,
  });
}

export function enrichMapsCensusRun(auth: Auth, runId: string) {
  return apiRequest<MapsCensusRunDetail>(`/maps/runs/${runId}/enrich`, {
    method: "POST",
    token: auth.token,
    orgId: auth.orgId,
  });
}

/** Path (relative to the API base) for a facility photo — fetched as a blob by
 * `MapsPlacePhoto` since it needs the Authorization header, unlike a plain `<img src>`.
 */
export function mapsPlacePhotoPath(runId: string, placeId: string): string {
  return `/maps/runs/${runId}/places/${placeId}/photo`;
}

export function mapsCensusExportPath(runId: string, tier: "all" | "verified" | "flagged" = "all"): string {
  const params = new URLSearchParams();
  if (tier !== "all") params.set("tier", tier);
  const query = params.toString();
  return `/maps/runs/${runId}/export.csv${query ? `?${query}` : ""}`;
}

/** Excel export path, scoped to a phase: "phase1" (every non-removed discovered
 * row) or "phase2" (the final keep/drop-eligible list, default). */
export function mapsCensusExportXlsxPath(runId: string, scope: "phase1" | "phase2" = "phase2"): string {
  const params = new URLSearchParams();
  if (scope !== "phase2") params.set("scope", scope);
  const query = params.toString();
  return `/maps/runs/${runId}/export.xlsx${query ? `?${query}` : ""}`;
}

export function parseExportFilename(disposition: string | null, fallback: string): string {
  const match = (disposition ?? "").match(/filename="([^"]+)"/);
  return match?.[1] ?? fallback;
}

/** Downloads the Excel workbook for a run, scoped to a phase. */
export async function downloadMapsCensusExport(
  auth: Auth,
  runId: string,
  scope: "phase1" | "phase2" = "phase2",
): Promise<void> {
  const response = await fetch(`${getApiBase()}${mapsCensusExportXlsxPath(runId, scope)}`, {
    headers: {
      Authorization: `Bearer ${auth.token}`,
      "X-Org-Id": auth.orgId,
    },
  });
  if (!response.ok) {
    throw new Error(`Export failed (${response.status})`);
  }
  const blob = await response.blob();
  const filename = parseExportFilename(
    response.headers.get("Content-Disposition"),
    `${runId}-maps-census-${scope}.xlsx`,
  );
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  anchor.rel = "noopener";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(objectUrl);
}
