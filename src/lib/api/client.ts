/** HTTP client for MultiAI Python backend */

import type { ApiError, ApiClientError as ApiClientErrorType } from "@/lib/api/types";

export { ApiClientError } from "@/lib/api/types";

const API_BASE = import.meta.env.VITE_API_URL ?? "/api/v1";

type RequestOptions = {
  method?: string;
  body?: unknown;
  token?: string | null;
  orgId?: string | null;
};

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = {
    Accept: "application/json",
  };

  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  if (options.token) {
    headers.Authorization = `Bearer ${options.token}`;
  }
  if (options.orgId) {
    headers["X-Org-Id"] = options.orgId;
  }

  const res = await fetch(`${API_BASE}${path}`, {
    method: options.method ?? (options.body !== undefined ? "POST" : "GET"),
    headers,
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    credentials: "include",
  });

  if (!res.ok) {
    let body: ApiError | undefined;
    try {
      body = (await res.json()) as ApiError;
    } catch {
      /* empty */
    }
    const { ApiClientError } = await import("@/lib/api/types");
    throw new ApiClientError(body?.message ?? res.statusText, res.status, body);
  }

  if (res.status === 204) {
    return undefined as T;
  }

  return (await res.json()) as T;
}

export function getApiBase(): string {
  return API_BASE;
}
