import { apiRequest } from "@/lib/api/client";
import type {
  ScrapingBlueprint,
  ScrapingMissionCreateInput,
  ScrapingMissionDetail,
  ScrapingMissionSummary,
} from "@/lib/scraping/types";

type Auth = { token: string; orgId: string };

export function createScrapingMission(auth: Auth, data: ScrapingMissionCreateInput) {
  return apiRequest<ScrapingMissionDetail>("/scraping/missions", {
    method: "POST",
    body: data,
    token: auth.token,
    orgId: auth.orgId,
  });
}

export function listScrapingMissions(auth: Auth) {
  return apiRequest<ScrapingMissionSummary[]>("/scraping/missions", {
    token: auth.token,
    orgId: auth.orgId,
  });
}

export function getScrapingMission(auth: Auth, missionId: string) {
  return apiRequest<ScrapingMissionDetail>(`/scraping/missions/${missionId}`, {
    token: auth.token,
    orgId: auth.orgId,
  });
}

export function generateScrapingBlueprint(auth: Auth, missionId: string) {
  return apiRequest<ScrapingBlueprint>(`/scraping/missions/${missionId}/blueprints`, {
    method: "POST",
    body: {},
    token: auth.token,
    orgId: auth.orgId,
    timeoutMs: 180_000,
  });
}

export function listScrapingBlueprints(auth: Auth, missionId: string) {
  return apiRequest<ScrapingBlueprint[]>(`/scraping/missions/${missionId}/blueprints`, {
    token: auth.token,
    orgId: auth.orgId,
  });
}

export function getScrapingBlueprint(auth: Auth, blueprintId: string) {
  return apiRequest<ScrapingBlueprint>(`/scraping/blueprints/${blueprintId}`, {
    token: auth.token,
    orgId: auth.orgId,
  });
}

export function approveScrapingBlueprint(auth: Auth, blueprintId: string) {
  return apiRequest<ScrapingBlueprint>(`/scraping/blueprints/${blueprintId}/approve`, {
    method: "POST",
    body: {},
    token: auth.token,
    orgId: auth.orgId,
  });
}

export function rejectScrapingBlueprint(auth: Auth, blueprintId: string, reason: string) {
  return apiRequest<ScrapingBlueprint>(`/scraping/blueprints/${blueprintId}/reject`, {
    method: "POST",
    body: { reason },
    token: auth.token,
    orgId: auth.orgId,
  });
}

export function requestScrapingBlueprintChanges(
  auth: Auth,
  blueprintId: string,
  changeInstructions: string,
) {
  return apiRequest<ScrapingBlueprint>(`/scraping/blueprints/${blueprintId}/request-changes`, {
    method: "POST",
    body: { change_instructions: changeInstructions },
    token: auth.token,
    orgId: auth.orgId,
    timeoutMs: 180_000,
  });
}
