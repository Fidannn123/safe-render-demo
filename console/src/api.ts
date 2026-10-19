import { CORE_API_BASE, type CoreComponents } from "@safepay/api-client";

export type Session = CoreComponents["schemas"]["Session"];
export type Payment = CoreComponents["schemas"]["Payment"];
export type Decision = CoreComponents["schemas"]["Decision"];
export type Vendor = CoreComponents["schemas"]["Vendor"];
export type TenantStatus = CoreComponents["schemas"]["TenantStatus"];

export class ApiError extends Error {
  constructor(public readonly code: string, message: string, public readonly status: number) {
    super(message);
  }
}

export function csrfToken(): string {
  if (typeof document === "undefined") return "";
  return document.cookie.split("; ").find((value) => value.startsWith("csrftoken="))?.slice(10) ?? "";
}

export async function request<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    headers["X-CSRFToken"] = csrfToken();
    headers["Idempotency-Key"] = crypto.randomUUID();
  }
  const response = await fetch(`${CORE_API_BASE}${path}`, {
    method: body === undefined ? "GET" : "POST", credentials: "same-origin", headers,
    body: body === undefined ? undefined : JSON.stringify(body), signal,
  });
  if (!response.ok) {
    const problem = await response.json().catch(() => ({})) as { code?: string; detail?: string; title?: string };
    throw new ApiError(problem.code ?? `HTTP_${response.status}`, problem.detail ?? problem.title ?? "Request failed.", response.status);
  }
  return (response.status === 204 ? undefined : await response.json()) as T;
}

export const money = (minor: number | null, currency = "USD") => minor === null ? "—" :
  new Intl.NumberFormat("en-US", { style: "currency", currency }).format(minor / 100);

export const dateTime = (value: string | null) => value ? new Intl.DateTimeFormat("en-GB", {
  dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Baku",
}).format(new Date(value)) : "—";

export function errorText(error: unknown): string {
  return error instanceof ApiError ? `${error.code}: ${error.message}` : error instanceof Error ? error.message : "Unexpected error.";
}
