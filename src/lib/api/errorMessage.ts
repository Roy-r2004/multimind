import type { ApiError } from "@/lib/api/types";

export function resolveFailedResponseMessage(parsed: unknown, fallback: string): string {
  if (typeof parsed === "string" && parsed.trim()) {
    return parsed.trim();
  }
  if (!parsed || typeof parsed !== "object") {
    return fallback;
  }
  const record = parsed as Record<string, unknown>;
  if (typeof record.message === "string" && record.message.trim()) {
    return record.message.trim();
  }
  if (typeof record.detail === "string" && record.detail.trim()) {
    return record.detail.trim();
  }
  if (Array.isArray(record.detail) && record.detail.length > 0) {
    const parts = record.detail
      .map((item) => {
        if (typeof item === "string") return item;
        if (item && typeof item === "object" && "msg" in item) {
          const msg = (item as { msg?: unknown }).msg;
          return typeof msg === "string" ? msg : null;
        }
        return null;
      })
      .filter((part): part is string => Boolean(part));
    if (parts.length > 0) {
      return parts.join("; ");
    }
  }
  return fallback;
}

export function normalizeApiErrorBody(parsed: unknown, message: string): ApiError {
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    return { error: "HTTP_ERROR", message };
  }
  const record = parsed as Record<string, unknown>;
  return {
    error: typeof record.error === "string" ? record.error : "HTTP_ERROR",
    message,
    details: record.details ?? record.detail,
  };
}
