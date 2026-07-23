/** Typed API methods — one function per backend endpoint */

import { apiFormRequest, apiRequest } from "@/lib/api/client";
import type {
  ApiChat,
  ApiCostSummary,
  ApiAdminAuditLogList,
  ApiAdminAuditStats,
  ApiAdminBrainDetail,
  ApiAdminBrainSummary,
  ApiAdminChatDetail,
  ApiAdminChatSummary,
  ApiAdminCreateMemberInput,
  ApiAdminLessonSummary,
  ApiAdminMember,
  ApiAdminOverview,
  ApiAdminProjectSummary,
  ApiAdminUpdateMemberInput,
  ApiAdminUsage,
  ApiAdminUserDetail,
  ApiAdminUserSummary,
  ApiBrain,
  ApiContentLabel,
  ApiDiscussResponse,
  ApiSavedDocument,
  ApiSavedDocumentSuggest,
  ApiLessonDetail,
  ApiLessonListItem,
  ApiModel,
  ApiModelSearchResult,
  ApiModelSet,
  ApiPricingCatalog,
  ApiProject,
  ApiProjectDetail,
  ApiSavedVerdict,
  ApiSavedVerdictState,
  ApiSession,
  ApiPromptBuilderImproveRequest,
  ApiPromptBuilderImproveResponse,
  ApiShareLink,
  ApiSavedVerdictDelete,
  ApiSavedVerdictPurge,
  ApiSharedChat,
  ApiTemplate,
  ApiTranscriptionResponse,
  ApiTurn,
  CreateTranscriptionOptions,
  Strategy,
} from "@/lib/api/types";

export { streamTurn } from "@/lib/api/stream";

type Auth = { token: string; orgId?: string | null };

const TRANSCRIPTION_TIMEOUT_MS = 360_000;

function normalizedMimeType(type: string | undefined): string {
  return (type ?? "").split(";")[0]?.trim().toLowerCase() ?? "";
}

function genericRecordingFilename(type: string | undefined): string {
  switch (normalizedMimeType(type)) {
    case "audio/webm":
      return "recording.webm";
    case "audio/ogg":
      return "recording.ogg";
    case "audio/mp4":
      return "recording.mp4";
    case "audio/mpeg":
      return "recording.mp3";
    case "audio/wav":
    case "audio/x-wav":
      return "recording.wav";
    default:
      return "recording.audio";
  }
}

function safeFilename(name: string | undefined, fallback: string): string {
  const basename = name?.split(/[/\\]/).pop()?.trim();
  if (!basename) {
    return fallback;
  }

  const cleaned = basename.replace(/[^A-Za-z0-9._-]/g, "_");
  if (!cleaned || cleaned === "." || cleaned === "..") {
    return fallback;
  }
  return cleaned;
}

function isBrowserFile(file: Blob): file is File {
  return typeof File !== "undefined" && file instanceof File;
}

function transcriptionFilename(options: CreateTranscriptionOptions): string {
  const fallback = genericRecordingFilename(options.file.type);
  if (options.filename) {
    return safeFilename(options.filename, fallback);
  }
  if (isBrowserFile(options.file)) {
    return safeFilename(options.file.name, fallback);
  }
  return fallback;
}

export function buildTranscriptionFormData(options: CreateTranscriptionOptions): FormData {
  const formData = new FormData();
  formData.append("file", options.file, transcriptionFilename(options));
  formData.append("language", options.language ?? "auto");
  return formData;
}

export const api = {
  auth: {
    signIn: (data: { email: string; password: string }) =>
      apiRequest<{
        access_token: string;
        token_type?: string;
        user?: ApiSession["user"];
        organization?: ApiSession["organization"];
      }>("/auth/signin", { body: data }),

    session: (auth: Auth) =>
      apiRequest<ApiSession>("/auth/session", { token: auth.token, orgId: auth.orgId }),

    warm: () => apiRequest<{ status: string }>("/health"),
  },

  models: {
    list: (auth: Auth) =>
      apiRequest<ApiModel[]>("/models", { token: auth.token, orgId: auth.orgId }),

    search: (auth: Auth, q: string, limit = 30) =>
      apiRequest<ApiModelSearchResult[]>(
        `/models/search?q=${encodeURIComponent(q)}&limit=${limit}`,
        { token: auth.token, orgId: auth.orgId },
      ),

    add: (auth: Auth, openrouter_slug: string) =>
      apiRequest<ApiModel>("/models", {
        method: "POST",
        body: { openrouter_slug },
        token: auth.token,
        orgId: auth.orgId,
      }),

    remove: (auth: Auth, modelId: string) =>
      apiRequest<{ message: string }>(`/models/${encodeURIComponent(modelId)}`, {
        method: "DELETE",
        token: auth.token,
        orgId: auth.orgId,
      }),
  },

  chats: {
    list: (auth: Auth) => apiRequest<ApiChat[]>("/chats", { token: auth.token, orgId: auth.orgId }),

    create: (auth: Auth, data: { title?: string; project_id?: string | null }) =>
      apiRequest<ApiChat>("/chats", { body: data, token: auth.token, orgId: auth.orgId }),

    update: (auth: Auth, chatId: string, data: { title?: string; project_id?: string | null }) =>
      apiRequest<ApiChat>(`/chats/${chatId}`, {
        method: "PATCH",
        body: data,
        token: auth.token,
        orgId: auth.orgId,
      }),

    delete: (auth: Auth, chatId: string) =>
      apiRequest<{ message: string }>(`/chats/${chatId}`, {
        method: "DELETE",
        token: auth.token,
        orgId: auth.orgId,
      }),

    pinVerdict: (auth: Auth, chatId: string, verdictId: string) =>
      apiRequest<ApiChat>(`/chats/${chatId}/pinned-verdict`, {
        method: "PUT",
        body: { verdict_id: verdictId },
        token: auth.token,
        orgId: auth.orgId,
      }),

    unpinVerdict: (auth: Auth, chatId: string) =>
      apiRequest<ApiChat>(`/chats/${chatId}/pinned-verdict`, {
        method: "DELETE",
        token: auth.token,
        orgId: auth.orgId,
      }),

    listTurns: (auth: Auth, chatId: string) =>
      apiRequest<ApiTurn[]>(`/chats/${chatId}/turns`, { token: auth.token, orgId: auth.orgId }),

    createTurn: (
      auth: Auth,
      chatId: string,
      data: {
        user_message: string;
        model_set_id: string;
        decision_insurance_enabled?: boolean;
        custom_instructions?: string | null;
      },
    ) =>
      apiRequest<ApiTurn>(`/chats/${chatId}/turns`, {
        body: data,
        token: auth.token,
        orgId: auth.orgId,
      }),

    deleteTurn: (auth: Auth, chatId: string, turnId: string) =>
      apiRequest<{ turn_id: string; deleted: boolean }>(`/chats/${chatId}/turns/${turnId}`, {
        method: "DELETE",
        token: auth.token,
        orgId: auth.orgId,
      }),

    createShareLink: (auth: Auth, chatId: string) =>
      apiRequest<ApiShareLink>(`/chats/${chatId}/share`, {
        method: "POST",
        token: auth.token,
        orgId: auth.orgId,
      }),

    getTurn: (auth: Auth, turnId: string) =>
      apiRequest<ApiTurn>(`/chats/turns/${turnId}`, { token: auth.token, orgId: auth.orgId }),
  },

  share: {
    get: (token: string) => apiRequest<ApiSharedChat>(`/share/${token}`),
  },

  verdicts: {
    save: (auth: Auth, verdictId: string) =>
      apiRequest<ApiSavedVerdictState>(`/verdicts/${verdictId}/save`, {
        method: "POST",
        token: auth.token,
        orgId: auth.orgId,
      }),

    unsave: (auth: Auth, verdictId: string) =>
      apiRequest<ApiSavedVerdictState>(`/verdicts/${verdictId}/save`, {
        method: "DELETE",
        token: auth.token,
        orgId: auth.orgId,
      }),
  },

  savedVerdicts: {
    list: (auth: Auth) =>
      apiRequest<ApiSavedVerdict[]>("/saved-verdicts", {
        token: auth.token,
        orgId: auth.orgId,
      }),

    delete: (auth: Auth, savedVerdictId: string) =>
      apiRequest<ApiSavedVerdictDelete>(`/saved-verdicts/${savedVerdictId}`, {
        method: "DELETE",
        token: auth.token,
        orgId: auth.orgId,
      }),

    purgeOrganization: (auth: Auth) =>
      apiRequest<ApiSavedVerdictPurge>("/saved-verdicts", {
        method: "DELETE",
        token: auth.token,
        orgId: auth.orgId,
      }),
  },

  contentLabels: {
    list: (auth: Auth) =>
      apiRequest<ApiContentLabel[]>("/content-labels", {
        token: auth.token,
        orgId: auth.orgId,
      }),

    create: (auth: Auth, name: string) =>
      apiRequest<ApiContentLabel>("/content-labels", {
        body: { name },
        token: auth.token,
        orgId: auth.orgId,
      }),

    rename: (auth: Auth, labelId: string, name: string) =>
      apiRequest<ApiContentLabel>(`/content-labels/${labelId}`, {
        method: "PATCH",
        body: { name },
        token: auth.token,
        orgId: auth.orgId,
      }),

    delete: (auth: Auth, labelId: string) =>
      apiRequest<{ message: string }>(`/content-labels/${labelId}`, {
        method: "DELETE",
        token: auth.token,
        orgId: auth.orgId,
      }),
  },

  savedDocuments: {
    list: (auth: Auth, params?: { q?: string; label_id?: string }) => {
      const search = new URLSearchParams();
      if (params?.q) search.set("q", params.q);
      if (params?.label_id) search.set("label_id", params.label_id);
      const qs = search.toString();
      return apiRequest<ApiSavedDocument[]>(`/saved-documents${qs ? `?${qs}` : ""}`, {
        token: auth.token,
        orgId: auth.orgId,
      });
    },

    suggest: (auth: Auth, turnId: string) =>
      apiRequest<ApiSavedDocumentSuggest>("/saved-documents/suggest", {
        body: { turn_id: turnId },
        token: auth.token,
        orgId: auth.orgId,
      }),

    create: (
      auth: Auth,
      data: {
        turn_id: string;
        name?: string | null;
        label_ids?: string[];
        label_names?: string[];
      },
    ) =>
      apiRequest<ApiSavedDocument>("/saved-documents", {
        body: data,
        token: auth.token,
        orgId: auth.orgId,
      }),

    update: (
      auth: Auth,
      documentId: string,
      data: { name?: string; label_ids?: string[] },
    ) =>
      apiRequest<ApiSavedDocument>(`/saved-documents/${documentId}`, {
        method: "PATCH",
        body: data,
        token: auth.token,
        orgId: auth.orgId,
      }),

    delete: (auth: Auth, documentId: string) =>
      apiRequest<{ message: string }>(`/saved-documents/${documentId}`, {
        method: "DELETE",
        token: auth.token,
        orgId: auth.orgId,
      }),
  },

  projects: {
    list: (auth: Auth) =>
      apiRequest<ApiProject[]>("/projects", { token: auth.token, orgId: auth.orgId }),

    get: (auth: Auth, projectId: string) =>
      apiRequest<ApiProjectDetail>(`/projects/${projectId}`, {
        token: auth.token,
        orgId: auth.orgId,
      }),

    create: (auth: Auth, data: { name: string; description?: string }) =>
      apiRequest<ApiProject>("/projects", { body: data, token: auth.token, orgId: auth.orgId }),

    delete: (auth: Auth, projectId: string) =>
      apiRequest<{ message: string }>(`/projects/${projectId}`, {
        method: "DELETE",
        token: auth.token,
        orgId: auth.orgId,
      }),

    update: (auth: Auth, projectId: string, data: { name?: string; description?: string | null }) =>
      apiRequest<ApiProjectDetail>(`/projects/${projectId}`, {
        method: "PATCH",
        body: data,
        token: auth.token,
        orgId: auth.orgId,
      }),
  },

  modelSets: {
    list: (auth: Auth) =>
      apiRequest<ApiModelSet[]>("/model-sets", { token: auth.token, orgId: auth.orgId }),

    create: (
      auth: Auth,
      data: {
        name: string;
        description?: string;
        models: string[];
        verdict_model: string;
        strategy?: Strategy;
        best_for?: string;
        template_name?: string | null;
        custom_instructions?: string | null;
      },
    ) =>
      apiRequest<ApiModelSet>("/model-sets", { body: data, token: auth.token, orgId: auth.orgId }),

    update: (
      auth: Auth,
      slug: string,
      data: Partial<{
        name: string;
        description: string;
        models: string[];
        verdict_model: string;
        strategy: Strategy;
        best_for: string;
        custom_instructions: string | null;
      }>,
    ) =>
      apiRequest<ApiModelSet>(`/model-sets/${slug}`, {
        method: "PATCH",
        body: data,
        token: auth.token,
        orgId: auth.orgId,
      }),

    delete: (auth: Auth, slug: string) =>
      apiRequest<{ message: string }>(`/model-sets/${slug}`, {
        method: "DELETE",
        token: auth.token,
        orgId: auth.orgId,
      }),
  },

  templates: {
    list: (auth: Auth) =>
      apiRequest<ApiTemplate[]>("/templates", { token: auth.token, orgId: auth.orgId }),

    create: (
      auth: Auth,
      data: { title: string; description?: string; category: string; instructions: string },
    ) =>
      apiRequest<ApiTemplate>("/templates", {
        method: "POST",
        body: data,
        token: auth.token,
        orgId: auth.orgId,
      }),
  },

  promptBuilder: {
    improve: (auth: Auth, data: ApiPromptBuilderImproveRequest) =>
      apiRequest<ApiPromptBuilderImproveResponse>("/prompt-builder/improve", {
        body: data,
        token: auth.token,
        orgId: auth.orgId,
      }),
  },

  transcriptions: {
    create: (auth: Auth, options: CreateTranscriptionOptions) => {
      const formData = buildTranscriptionFormData(options);

      return apiFormRequest<ApiTranscriptionResponse>("/transcriptions", {
        formData,
        token: auth.token,
        orgId: auth.orgId,
        timeoutMs: TRANSCRIPTION_TIMEOUT_MS,
        signal: options.signal,
      });
    },
  },

  costs: {
    summary: (auth: Auth) =>
      apiRequest<ApiCostSummary>("/costs/summary", { token: auth.token, orgId: auth.orgId }),

    pricing: (auth: Auth) =>
      apiRequest<ApiPricingCatalog>("/costs/pricing", { token: auth.token, orgId: auth.orgId }),
  },

  admin: {
    overview: (auth: Auth) =>
      apiRequest<ApiAdminOverview>("/admin/overview", { token: auth.token, orgId: auth.orgId }),

    members: (auth: Auth) =>
      apiRequest<ApiAdminMember[]>("/admin/members", { token: auth.token, orgId: auth.orgId }),

    createMember: (auth: Auth, data: ApiAdminCreateMemberInput) =>
      apiRequest<ApiAdminMember>("/admin/members", {
        method: "POST",
        body: data,
        token: auth.token,
        orgId: auth.orgId,
      }),

    updateMember: (auth: Auth, membershipId: string, data: ApiAdminUpdateMemberInput) =>
      apiRequest<ApiAdminMember>(`/admin/members/${membershipId}`, {
        method: "PATCH",
        body: data,
        token: auth.token,
        orgId: auth.orgId,
      }),

    removeMember: (auth: Auth, membershipId: string) =>
      apiRequest<{ message: string }>(`/admin/members/${membershipId}`, {
        method: "DELETE",
        token: auth.token,
        orgId: auth.orgId,
      }),

    usage: (auth: Auth) =>
      apiRequest<ApiAdminUsage>("/admin/usage", { token: auth.token, orgId: auth.orgId }),

    auditLogs: (
      auth: Auth,
      params?: {
        q?: string;
        category?: string;
        action?: string;
        actor_user_id?: string;
        target_user_id?: string;
        severity?: string;
        page?: number;
        limit?: number;
      },
    ) => {
      const search = new URLSearchParams();
      if (params?.q) search.set("q", params.q);
      if (params?.category) search.set("category", params.category);
      if (params?.action) search.set("action", params.action);
      if (params?.actor_user_id) search.set("actor_user_id", params.actor_user_id);
      if (params?.target_user_id) search.set("target_user_id", params.target_user_id);
      if (params?.severity) search.set("severity", params.severity);
      if (params?.page) search.set("page", String(params.page));
      if (params?.limit) search.set("limit", String(params.limit));
      const qs = search.toString();
      return apiRequest<ApiAdminAuditLogList>(`/admin/audit-logs${qs ? `?${qs}` : ""}`, {
        token: auth.token,
        orgId: auth.orgId,
      });
    },

    auditStats: (auth: Auth) =>
      apiRequest<ApiAdminAuditStats>("/admin/audit-logs/stats", {
        token: auth.token,
        orgId: auth.orgId,
      }),

    users: (auth: Auth) =>
      apiRequest<ApiAdminUserSummary[]>("/admin/users", { token: auth.token, orgId: auth.orgId }),

    user: (auth: Auth, userId: string) =>
      apiRequest<ApiAdminUserDetail>(`/admin/users/${userId}`, {
        token: auth.token,
        orgId: auth.orgId,
      }),

    userChats: (auth: Auth, userId: string) =>
      apiRequest<ApiAdminChatSummary[]>(`/admin/users/${userId}/chats`, {
        token: auth.token,
        orgId: auth.orgId,
      }),

    userActivity: (auth: Auth, userId: string, page = 1) =>
      apiRequest<ApiAdminAuditLogList>(`/admin/users/${userId}/activity?page=${page}&limit=50`, {
        token: auth.token,
        orgId: auth.orgId,
      }),

    chats: (auth: Auth, params?: { user_id?: string; q?: string }) => {
      const search = new URLSearchParams();
      if (params?.user_id) search.set("user_id", params.user_id);
      if (params?.q) search.set("q", params.q);
      const qs = search.toString();
      return apiRequest<ApiAdminChatSummary[]>(`/admin/chats${qs ? `?${qs}` : ""}`, {
        token: auth.token,
        orgId: auth.orgId,
      });
    },

    chat: (auth: Auth, chatId: string) =>
      apiRequest<ApiAdminChatDetail>(`/admin/chats/${chatId}`, {
        token: auth.token,
        orgId: auth.orgId,
      }),

    brains: (auth: Auth) =>
      apiRequest<ApiAdminBrainSummary[]>("/admin/brains", {
        token: auth.token,
        orgId: auth.orgId,
      }),

    brain: (auth: Auth, userId: string) =>
      apiRequest<ApiAdminBrainDetail>(`/admin/brains/${userId}`, {
        token: auth.token,
        orgId: auth.orgId,
      }),

    lessons: (auth: Auth) =>
      apiRequest<ApiAdminLessonSummary[]>("/admin/lessons", {
        token: auth.token,
        orgId: auth.orgId,
      }),

    projects: (auth: Auth) =>
      apiRequest<ApiAdminProjectSummary[]>("/admin/projects", {
        token: auth.token,
        orgId: auth.orgId,
      }),

    securityEvents: (auth: Auth, page = 1) =>
      apiRequest<ApiAdminAuditLogList>(`/admin/security/events?page=${page}&limit=50`, {
        token: auth.token,
        orgId: auth.orgId,
      }),
  },

  lessons: {
    list: (auth: Auth) =>
      apiRequest<ApiLessonListItem[]>("/lessons", { token: auth.token, orgId: auth.orgId }),

    get: (auth: Auth, lessonId: string) =>
      apiRequest<ApiLessonDetail>(`/lessons/${lessonId}`, { token: auth.token, orgId: auth.orgId }),

    disagree: (auth: Auth, turnId: string, data: { reason: string; user_position: string }) =>
      apiRequest<ApiLessonDetail>(`/lessons/turns/${turnId}/disagree`, {
        method: "POST",
        body: data,
        token: auth.token,
        orgId: auth.orgId,
      }),

    discussStart: (auth: Auth, turnId: string) =>
      apiRequest<ApiDiscussResponse>(`/lessons/turns/${turnId}/discuss/start`, {
        method: "POST",
        token: auth.token,
        orgId: auth.orgId,
        timeoutMs: 45_000,
      }),

    discuss: (auth: Auth, turnId: string, message: string) =>
      apiRequest<ApiDiscussResponse>(`/lessons/turns/${turnId}/discuss`, {
        method: "POST",
        body: { message },
        token: auth.token,
        orgId: auth.orgId,
        timeoutMs: 180_000,
      }),

    discussFinalize: (auth: Auth, turnId: string) =>
      apiRequest<{ lesson: ApiLessonDetail }>(`/lessons/turns/${turnId}/discuss/finalize`, {
        method: "POST",
        token: auth.token,
        orgId: auth.orgId,
        timeoutMs: 120_000,
      }),

    delete: (auth: Auth, lessonId: string) =>
      apiRequest<{ message: string }>(`/lessons/${lessonId}`, {
        method: "DELETE",
        token: auth.token,
        orgId: auth.orgId,
      }),
  },

  brain: {
    get: (auth: Auth) => apiRequest<ApiBrain>("/brain", { token: auth.token, orgId: auth.orgId }),
  },
};
