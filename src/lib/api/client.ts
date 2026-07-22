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

type FormRequestOptions = {
  method?: string;
  formData: FormData;
  token?: string | null;
  orgId?: string | null;
  timeoutMs?: number;
  signal?: AbortSignal;
};

type FetchRequestOptions = {
  method?: string;
  headers: Record<string, string>;
  body?: BodyInit;
  timeoutMs: number;
  signal?: AbortSignal;
};

function authHeaders(token?: string | null, orgId?: string | null): Record<string, string> {
  const headers: Record<string, string> = {};
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  if (orgId) {
    headers["X-Org-Id"] = orgId;
  }
  return headers;
}

function isAbortError(err: unknown): boolean {
  return (
    (typeof DOMException !== "undefined" &&
      err instanceof DOMException &&
      err.name === "AbortError") ||
    (err instanceof Error && err.name === "AbortError")
  );
}

function mergedAbortSignal(timeoutMs: number, callerSignal?: AbortSignal) {
  const controller = new AbortController();
  let timeoutTriggered = false;

  const abortFromCaller = () => controller.abort();
  if (callerSignal?.aborted) {
    controller.abort();
  } else {
    callerSignal?.addEventListener("abort", abortFromCaller, { once: true });
  }

  const timer = setTimeout(() => {
    timeoutTriggered = true;
    controller.abort();
  }, timeoutMs);

  return {
    signal: controller.signal,
    wasCallerAborted: () => Boolean(callerSignal?.aborted) && !timeoutTriggered,
    wasTimeout: () => timeoutTriggered,
    cleanup: () => {
      clearTimeout(timer);
      callerSignal?.removeEventListener("abort", abortFromCaller);
    },
  };
}

async function parseResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let body: ApiError | undefined;
    try {
      body = (await res.json()) as ApiError;
    } catch {
      /* empty */
    }
    const { ApiClientError } = await import("@/lib/api/types");
    throw new ApiClientError(
      body?.message ?? res.statusText,
      res.status,
      body,
      res.headers.get("Retry-After"),
    );
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

async function fetchRequest<T>(path: string, options: FetchRequestOptions): Promise<T> {
  const abort = mergedAbortSignal(options.timeoutMs, options.signal);

  let res: Response;
  try {
    res = await fetch(`${resolveApiBase()}${path}`, {
      method: options.method,
      headers: options.headers,
      body: options.body,
      credentials: "include",
      signal: abort.signal,
    });
  } catch (err) {
    const { ApiClientError } = await import("@/lib/api/types");
    if (isAbortError(err)) {
      if (abort.wasCallerAborted()) {
        throw new ApiClientError("Request was cancelled.", 0, {
          error: "REQUEST_CANCELLED",
          message: "Request was cancelled.",
        });
      }
      throw new ApiClientError(
        "API is taking too long to respond. It may be waking up or redeploying — wait ~30s and try again.",
        408,
        {
          error: abort.wasTimeout() ? "REQUEST_TIMEOUT" : "REQUEST_ABORTED",
          message: "API is taking too long to respond.",
        },
      );
    }
    throw new ApiClientError(
      "Cannot reach the API. Check that the backend service is running and reachable.",
      0,
    );
  } finally {
    abort.cleanup();
  }

  return parseResponse<T>(res);
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...authHeaders(options.token, options.orgId),
  };

  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }

  return fetchRequest<T>(path, {
    method: options.method ?? (options.body !== undefined ? "POST" : "GET"),
    headers,
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    timeoutMs: options.timeoutMs ?? DEFAULT_TIMEOUT_MS,
  });
}

export async function apiFormRequest<T>(path: string, options: FormRequestOptions): Promise<T> {
  return fetchRequest<T>(path, {
    method: options.method ?? "POST",
    headers: {
      Accept: "application/json",
      ...authHeaders(options.token, options.orgId),
    },
    body: options.formData,
    timeoutMs: options.timeoutMs ?? DEFAULT_TIMEOUT_MS,
    signal: options.signal,
  });
}

export function getApiBase(): string {
  return resolveApiBase();
}
