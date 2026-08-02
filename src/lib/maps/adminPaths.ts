import type { MapsAdminCellFilters, MapsAdminPlaceFilters } from "@/lib/maps/adminTypes";

function appendParam(params: URLSearchParams, key: string, value: string | number | boolean | undefined) {
  if (value === undefined || value === "") return;
  if (typeof value === "boolean") {
    if (value) params.set(key, "true");
    return;
  }
  params.set(key, String(value));
}

function withQuery(path: string, params: URLSearchParams): string {
  const query = params.toString();
  return query ? `${path}?${query}` : path;
}

export function mapsAdminDashboardPath(runId: string): string {
  return `/maps/runs/${runId}/dashboard`;
}

export function mapsAdminRegionsPath(runId: string, limit = 50, offset = 0): string {
  const params = new URLSearchParams();
  appendParam(params, "limit", limit);
  appendParam(params, "offset", offset);
  return withQuery(`/maps/runs/${runId}/regions`, params);
}

export function mapsAdminCellsPath(runId: string, filters: MapsAdminCellFilters = {}): string {
  const params = new URLSearchParams();
  appendParam(params, "status", filters.status);
  appendParam(params, "region", filters.region);
  appendParam(params, "query_family", filters.query_family);
  appendParam(params, "query_language", filters.query_language);
  appendParam(params, "capped_only", filters.capped_only);
  appendParam(params, "failed_only", filters.failed_only);
  appendParam(params, "expanded_only", filters.expanded_only);
  appendParam(params, "limit", filters.limit ?? 50);
  appendParam(params, "offset", filters.offset ?? 0);
  return withQuery(`/maps/runs/${runId}/cells/paged`, params);
}

export function mapsAdminPlacesPath(runId: string, filters: MapsAdminPlaceFilters = {}): string {
  const params = new URLSearchParams();
  appendParam(params, "search", filters.search);
  appendParam(params, "client_eligibility", filters.client_eligibility);
  appendParam(params, "lifecycle_status", filters.lifecycle_status);
  appendParam(params, "limit", filters.limit ?? 50);
  appendParam(params, "offset", filters.offset ?? 0);
  return withQuery(`/maps/runs/${runId}/places/paged`, params);
}

export function mapsAdminPlaceDetailPath(runId: string, placeId: string): string {
  return `/maps/runs/${runId}/places/${placeId}`;
}

export function mapsAdminPlaceReviewPath(runId: string, placeId: string): string {
  return `/maps/runs/${runId}/places/${placeId}/review`;
}

export function mapsAdminExportSummaryPath(runId: string): string {
  return `/maps/runs/${runId}/export-summary`;
}

export function mapsAdminPausePath(runId: string): string {
  return `/maps/runs/${runId}/pause`;
}

export function mapsAdminResumePath(runId: string): string {
  return `/maps/runs/${runId}/resume`;
}

export function mapsAdminCancelPath(runId: string): string {
  return `/maps/runs/${runId}/cancel`;
}

export function mapsAdminRetryFailedCellsPath(runId: string): string {
  return `/maps/runs/${runId}/retry-failed-cells`;
}

export function mapsAdminRetryWebsitesPath(runId: string): string {
  return `/maps/runs/${runId}/retry-websites`;
}

export function mapsAdminRetryEnrichmentPath(runId: string): string {
  return `/maps/runs/${runId}/retry-enrichment`;
}
