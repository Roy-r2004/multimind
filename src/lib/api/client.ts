/** HTTP client for MultiAI Python backend */

import type { ApiError } from "@/lib/api/types";

export { ApiClientError } from "@/lib/api/types";

const DEFAULT_TIMEOUT_MS = 60_000;

function resolveApiBase(): string {
  return (import.meta.env.VITE_API_URL ?? "/api/v1").replace(/\/$/, "");
}

type RequestOptions = {
  method?: string;
  body?: unknown;
  token?: string | null;
  orgId?: string | null;
  timeoutMs?: number;
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

  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  let res: Response;
  try {
    res = await fetch(`${resolveApiBase()}${path}`, {
      method: options.method ?? (options.body !== undefined ? "POST" : "GET"),
      headers,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      credentials: "include",
      signal: controller.signal,
    });
  } catch (err) {
    const { ApiClientError } = await import("@/lib/api/types");
    const aborted =
      (typeof DOMException !== "undefined" &&
        err instanceof DOMException &&
        err.name === "AbortError") ||
      (err instanceof Error && err.name === "AbortError");
    if (aborted) {
      throw new ApiClientError(
        "API is taking too long to respond. It may be waking up or redeploying — wait ~30s and try again.",
        408,
      );
    }
    throw new ApiClientError(
      "Cannot reach the API. Check that the backend service is running and reachable.",
      0,
    );
  } finally {
    clearTimeout(timer);
  }

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

  const text = await res.text();
  try {
    return JSON.parse(text) as T;
  } catch {
    const { ApiClientError } = await import("@/lib/api/types");
    throw new ApiClientError(
      "The API returned an incomplete or invalid JSON response.",
      res.status,
    );
  }
}

export function getApiBase(): string {
  return resolveApiBase();
}
