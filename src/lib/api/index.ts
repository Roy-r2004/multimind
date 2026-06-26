/** Typed API methods — one function per backend endpoint */

import { apiRequest } from "@/lib/api/client";
import type {
  ApiChat,
  ApiCostSummary,
  ApiAdminCreateMemberInput,
  ApiAdminMember,
  ApiAdminOverview,
  ApiAdminUpdateMemberInput,
  ApiAdminUsage,
  ApiBrain,
  ApiLessonDetail,
  ApiLessonListItem,
  ApiModel,
  ApiModelSearchResult,
  ApiModelSet,
  ApiPricingCatalog,
  ApiProject,
  ApiSession,
  ApiShareLink,
  ApiSharedChat,
  ApiTemplate,
  ApiTurn,
  Strategy,
} from "@/lib/api/types";

export { streamTurn } from "@/lib/api/stream";

type Auth = { token: string; orgId: string };

export const api = {
  auth: {
    signIn: (data: { email: string; password: string }) =>
      apiRequest<{ access_token: string }>("/auth/signin", { body: data }),

    session: (auth: Auth) =>
      apiRequest<ApiSession>("/auth/session", { token: auth.token, orgId: auth.orgId }),
  },

  models: {
    list: (auth: Auth) =>
      apiRequest<ApiModel[]>("/models", { token: auth.token, orgId: auth.orgId }),

    search: (auth: Auth, q: string, limit = 20) =>
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

  projects: {
    list: (auth: Auth) =>
      apiRequest<ApiProject[]>("/projects", { token: auth.token, orgId: auth.orgId }),

    create: (auth: Auth, data: { name: string; description?: string }) =>
      apiRequest<ApiProject>("/projects", { body: data, token: auth.token, orgId: auth.orgId }),
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
  },

  brain: {
    get: (auth: Auth) => apiRequest<ApiBrain>("/brain", { token: auth.token, orgId: auth.orgId }),
  },
};
