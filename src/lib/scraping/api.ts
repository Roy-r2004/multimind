import { apiRequest, getApiBase } from "@/lib/api/client";
import type {
  ScrapingCoverageCell,
  ScrapingBlueprint,
  ScrapingEvent,
  ScrapingExecutionDetail,
  ScrapingExecutionSummary,
  ScrapingMissionCreateInput,
  ScrapingMissionDetail,
  ScrapingMissionSummary,
  ScrapingMissionUpdateInput,
  ScrapingRunDetail,
  ScrapingRunSummary,
  ScrapingTask,
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

export function updateScrapingMission(
  auth: Auth,
  missionId: string,
  data: ScrapingMissionUpdateInput,
) {
  return apiRequest<ScrapingMissionDetail>(`/scraping/missions/${missionId}`, {
    method: "PATCH",
    body: data,
    token: auth.token,
    orgId: auth.orgId,
  });
}

export function deleteScrapingMission(auth: Auth, missionId: string) {
  return apiRequest<void>(`/scraping/missions/${missionId}`, {
    method: "DELETE",
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

export function renameScrapingBlueprint(auth: Auth, blueprintId: string, name: string) {
  return apiRequest<ScrapingBlueprint>(`/scraping/blueprints/${blueprintId}/rename`, {
    method: "PATCH",
    body: { name },
    token: auth.token,
    orgId: auth.orgId,
  });
}

export function deleteScrapingBlueprint(auth: Auth, blueprintId: string) {
  return apiRequest<void>(`/scraping/blueprints/${blueprintId}`, {
    method: "DELETE",
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

export function planScrapingTeam(auth: Auth, missionId: string) {
  return apiRequest<ScrapingRunDetail>(`/scraping/missions/${missionId}/runs/plan`, {
    method: "POST",
    body: {},
    token: auth.token,
    orgId: auth.orgId,
    timeoutMs: 180_000,
  });
}

export function listScrapingRuns(auth: Auth, missionId: string) {
  return apiRequest<ScrapingRunSummary[]>(`/scraping/missions/${missionId}/runs`, {
    token: auth.token,
    orgId: auth.orgId,
  });
}

export function getScrapingRun(auth: Auth, runId: string) {
  return apiRequest<ScrapingRunDetail>(`/scraping/runs/${runId}`, {
    token: auth.token,
    orgId: auth.orgId,
  });
}

export function cancelScrapingRun(auth: Auth, runId: string) {
  return apiRequest<ScrapingRunDetail>(`/scraping/runs/${runId}/cancel`, {
    method: "POST",
    body: {},
    token: auth.token,
    orgId: auth.orgId,
  });
}

export function deleteScrapingRun(auth: Auth, runId: string) {
  return apiRequest<void>(`/scraping/runs/${runId}`, {
    method: "DELETE",
    token: auth.token,
    orgId: auth.orgId,
  });
}

export function createScrapingExecution(auth: Auth, runId: string) {
  return apiRequest<ScrapingExecutionSummary>(`/scraping/runs/${runId}/executions`, {
    method: "POST",
    body: { execution_type: "initial_full_country", mode: "mock" },
    token: auth.token,
    orgId: auth.orgId,
  });
}

export function listScrapingExecutions(auth: Auth, runId: string) {
  return apiRequest<ScrapingExecutionSummary[]>(`/scraping/runs/${runId}/executions`, {
    token: auth.token,
    orgId: auth.orgId,
  });
}

export function getScrapingExecution(auth: Auth, executionId: string) {
  return apiRequest<ScrapingExecutionDetail>(`/scraping/executions/${executionId}`, {
    token: auth.token,
    orgId: auth.orgId,
  });
}

export function listScrapingExecutionTasks(auth: Auth, executionId: string) {
  return apiRequest<ScrapingTask[]>(`/scraping/executions/${executionId}/tasks`, {
    token: auth.token,
    orgId: auth.orgId,
  });
}

export function listScrapingExecutionCoverage(auth: Auth, executionId: string) {
  return apiRequest<ScrapingCoverageCell[]>(`/scraping/executions/${executionId}/coverage`, {
    token: auth.token,
    orgId: auth.orgId,
  });
}

export function listScrapingExecutionEvents(
  auth: Auth,
  executionId: string,
  afterSequence?: number,
) {
  const query = afterSequence ? `?after_sequence=${afterSequence}` : "";
  return apiRequest<ScrapingEvent[]>(`/scraping/executions/${executionId}/events${query}`, {
    token: auth.token,
    orgId: auth.orgId,
  });
}

export function cancelScrapingExecution(auth: Auth, executionId: string) {
  return apiRequest<ScrapingExecutionSummary>(`/scraping/executions/${executionId}/cancel`, {
    method: "POST",
    body: {},
    token: auth.token,
    orgId: auth.orgId,
  });
}

export function deleteScrapingExecution(auth: Auth, executionId: string) {
  return apiRequest<void>(`/scraping/executions/${executionId}`, {
    method: "DELETE",
    token: auth.token,
    orgId: auth.orgId,
  });
}

export function scrapingExecutionStreamUrl(executionId: string, afterSequence: number) {
  const params = afterSequence > 0 ? `?after_sequence=${afterSequence}` : "";
  return `${getApiBase()}/scraping/executions/${executionId}/events/stream${params}`;
}
