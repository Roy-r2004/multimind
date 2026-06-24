/** Typed API methods — one function per backend endpoint */

import { apiRequest } from "@/lib/api/client";
import type {
  ApiChat,
  ApiCostSummary,
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
    signUp: (data: { email: string; password: string; full_name: string; org_name?: string }) =>
      apiRequest<{ access_token: string }>("/auth/signup", { body: data }),

    signIn: (data: { email: string; password: string }) =>
      apiRequest<{ access_token: string }>("/auth/signin", { body: data }),

    session: (auth: Auth) =>
      apiRequest<ApiSession>("/auth/session", { token: auth.token, orgId: auth.orgId }),
  },

  models: {
    list: (auth: Auth) => apiRequest<ApiModel[]>("/models", { token: auth.token, orgId: auth.orgId }),

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
};
