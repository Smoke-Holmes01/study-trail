import { ApiError } from "./api";
export interface Admin {
  id: string;
  login: string;
  display_name: string;
  role: "admin";
}
export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}
export interface Config {
  id: string;
  kind: string;
  stable_id: string;
  name: string;
  description: string;
  enabled: boolean;
  purpose: string | null;
  lock_version: number;
  revision_no: number;
  payload: Record<string, any>;
  secret_slots: Record<string, { has_value: boolean }>;
}
let csrf = "";
export function adminCsrf(value: string) {
  csrf = value;
}
export async function adminApi<T = any>(
  path: string,
  method = "GET",
  body?: unknown,
  key?: string,
): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") headers["X-CSRF-Token"] = csrf;
  if (!["GET", "HEAD"].includes(method) && !path.startsWith("/auth/"))
    headers["Idempotency-Key"] = key ?? crypto.randomUUID();
  let response: Response;
  try {
    response = await fetch("/api/admin" + path, {
      method,
      headers,
      credentials: "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(
      "NETWORK_ERROR",
      "连接中断，操作状态待确认。请重新载入记录后核对。",
      0,
    );
  }
  const result = await response.json();
  if (!response.ok) {
    if (response.status === 401 && !path.startsWith("/auth/") && path !== "/me")
      window.dispatchEvent(new Event("study-trail:admin-unauthorized"));
    throw new ApiError(
      result.error?.code ?? "SERVICE_UNAVAILABLE",
      result.error?.message ?? "服务暂时不可用",
      response.status,
      result.error?.details,
    );
  }
  return result.data as T;
}
