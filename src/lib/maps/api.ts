import { apiRequest, getApiBase } from "@/lib/api/client";
import type {
  MapsCensusCellItem,
  MapsCensusRunCreateInput,
  MapsCensusRunDetail,
  MapsCensusRunSummary,
  MapsPlaceItem,
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

export function listMapsCensusCells(auth: Auth, runId: string) {
  return apiRequest<MapsCensusCellItem[]>(`/maps/runs/${runId}/cells`, {
    token: auth.token,
    orgId: auth.orgId,
  });
}

export function listMapsCensusPlaces(
  auth: Auth,
  runId: string,
  filters: { relevantOnly?: boolean; withWebsiteOnly?: boolean } = {},
) {
  const params = new URLSearchParams();
  if (filters.relevantOnly) params.set("relevant_only", "true");
  if (filters.withWebsiteOnly) params.set("with_website_only", "true");
  const query = params.toString();
  return apiRequest<MapsPlaceItem[]>(`/maps/runs/${runId}/places${query ? `?${query}` : ""}`, {
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

export async function downloadMapsCensusExport(
  auth: Auth,
  runId: string,
  tier: "all" | "verified" | "flagged" = "all",
): Promise<void> {
  const response = await fetch(`${getApiBase()}${mapsCensusExportPath(runId, tier)}`, {
    headers: {
      Authorization: `Bearer ${auth.token}`,
      "X-Org-Id": auth.orgId,
    },
  });
  if (!response.ok) {
    throw new Error(`Export failed (${response.status})`);
  }
  const blob = await response.blob();
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const match = disposition.match(/filename="([^"]+)"/);
  const filename = match?.[1] ?? `${runId}-maps-census-export.csv`;
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(objectUrl);
}
