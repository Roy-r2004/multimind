import { apiRequest } from "@/lib/api/client";
import {
  mapsAdminCancelPath,
  mapsAdminCellsPath,
  mapsAdminDashboardPath,
  mapsAdminExportSummaryPath,
  mapsAdminPausePath,
  mapsAdminPlaceDetailPath,
  mapsAdminPlaceReviewPath,
  mapsAdminPlacesPath,
  mapsAdminRegionsPath,
  mapsAdminReconcileFinalizationPath,
  mapsAdminRetryEnrichmentPath,
  mapsAdminRetryFailedCellsPath,
  mapsAdminRetryWebsitesPath,
  mapsAdminResumePath,
} from "@/lib/maps/adminPaths";
import type {
  MapsAdminCellFilters,
  MapsAdminPlaceFilters,
  MapsCampaignActionResponse,
  MapsCellListResponse,
  MapsCensusRunAdminDetail,
  MapsExportSummaryResponse,
  MapsPlaceDetail,
  MapsPlaceListResponse,
  MapsPlaceReviewRequest,
  MapsRegionListResponse,
} from "@/lib/maps/adminTypes";

export {
  mapsAdminCancelPath,
  mapsAdminCellsPath,
  mapsAdminDashboardPath,
  mapsAdminExportSummaryPath,
  mapsAdminPausePath,
  mapsAdminPlaceDetailPath,
  mapsAdminPlaceReviewPath,
  mapsAdminPlacesPath,
  mapsAdminRegionsPath,
  mapsAdminReconcileFinalizationPath,
  mapsAdminRetryEnrichmentPath,
  mapsAdminRetryFailedCellsPath,
  mapsAdminRetryWebsitesPath,
  mapsAdminResumePath,
} from "@/lib/maps/adminPaths";

type Auth = { token: string; orgId: string };

export function getMapsCensusAdminDashboard(auth: Auth, runId: string) {
  return apiRequest<MapsCensusRunAdminDetail>(mapsAdminDashboardPath(runId), {
    token: auth.token,
    orgId: auth.orgId,
  });
}

export function listMapsCensusAdminRegions(
  auth: Auth,
  runId: string,
  options: { limit?: number; offset?: number } = {},
) {
  return apiRequest<MapsRegionListResponse>(
    mapsAdminRegionsPath(runId, options.limit, options.offset),
    { token: auth.token, orgId: auth.orgId },
  );
}

export function listMapsCensusAdminCells(auth: Auth, runId: string, filters: MapsAdminCellFilters = {}) {
  return apiRequest<MapsCellListResponse>(mapsAdminCellsPath(runId, filters), {
    token: auth.token,
    orgId: auth.orgId,
  });
}

export function listMapsCensusAdminPlaces(auth: Auth, runId: string, filters: MapsAdminPlaceFilters = {}) {
  return apiRequest<MapsPlaceListResponse>(mapsAdminPlacesPath(runId, filters), {
    token: auth.token,
    orgId: auth.orgId,
  });
}

export function getMapsCensusAdminPlaceDetail(auth: Auth, runId: string, placeId: string) {
  return apiRequest<MapsPlaceDetail>(mapsAdminPlaceDetailPath(runId, placeId), {
    token: auth.token,
    orgId: auth.orgId,
  });
}

export function getMapsCensusExportSummary(auth: Auth, runId: string) {
  return apiRequest<MapsExportSummaryResponse>(mapsAdminExportSummaryPath(runId), {
    token: auth.token,
    orgId: auth.orgId,
  });
}

export function pauseMapsCensusRun(auth: Auth, runId: string) {
  return apiRequest<MapsCampaignActionResponse>(mapsAdminPausePath(runId), {
    method: "POST",
    token: auth.token,
    orgId: auth.orgId,
  });
}

export function resumeMapsCensusRun(auth: Auth, runId: string) {
  return apiRequest<MapsCampaignActionResponse>(mapsAdminResumePath(runId), {
    method: "POST",
    token: auth.token,
    orgId: auth.orgId,
  });
}

export function cancelMapsCensusRun(auth: Auth, runId: string) {
  return apiRequest<MapsCampaignActionResponse>(mapsAdminCancelPath(runId), {
    method: "POST",
    token: auth.token,
    orgId: auth.orgId,
  });
}

export function retryMapsCensusFailedCells(auth: Auth, runId: string) {
  return apiRequest<MapsCampaignActionResponse>(mapsAdminRetryFailedCellsPath(runId), {
    method: "POST",
    token: auth.token,
    orgId: auth.orgId,
  });
}

export function retryMapsCensusWebsites(auth: Auth, runId: string) {
  return apiRequest<MapsCampaignActionResponse>(mapsAdminRetryWebsitesPath(runId), {
    method: "POST",
    token: auth.token,
    orgId: auth.orgId,
  });
}

export function retryMapsCensusEnrichment(auth: Auth, runId: string) {
  return apiRequest<MapsCampaignActionResponse>(mapsAdminRetryEnrichmentPath(runId), {
    method: "POST",
    token: auth.token,
    orgId: auth.orgId,
  });
}

export function reconcileMapsCensusFinalization(
  auth: Auth,
  runId: string,
  options: { force?: boolean } = {},
) {
  return apiRequest<Record<string, unknown>>(
    mapsAdminReconcileFinalizationPath(runId, options.force === true),
    {
      method: "POST",
      token: auth.token,
      orgId: auth.orgId,
    },
  );
}

export function applyMapsPlaceReview(
  auth: Auth,
  runId: string,
  placeId: string,
  payload: MapsPlaceReviewRequest,
) {
  return apiRequest<MapsPlaceDetail>(mapsAdminPlaceReviewPath(runId, placeId), {
    method: "POST",
    body: payload,
    token: auth.token,
    orgId: auth.orgId,
  });
}
