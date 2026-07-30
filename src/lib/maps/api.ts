import { apiRequest } from "@/lib/api/client";
import type {
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
