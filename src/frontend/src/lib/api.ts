import type { components } from "../generated/api";
export type Student = components["schemas"]["StudentDTO"];
export type Skill = components["schemas"]["SkillDTO"];
export type MCPServer = components["schemas"]["MCPServerDTO"];
export type Agent = components["schemas"]["AgentDTO"];
export type Conversation = components["schemas"]["ConversationDTO"];
export type Message = components["schemas"]["MessageDTO"];
export type Attachment = components["schemas"]["AttachmentDTO"];
export type KnowledgeBase = components["schemas"]["KnowledgeBaseDTO"];
export type StoredFile = components["schemas"]["FileDTO"];
export type Plan = components["schemas"]["PlanDTO"];
export type Source = components["schemas"]["SourceDTO"];
export type Task = components["schemas"]["TaskDTO"];
export type ModelOption = components["schemas"]["ModelOptionDTO"];
export type Exercise = components["schemas"]["ExerciseDTO"];
export type ChatAccepted = components["schemas"]["ChatAcceptedDTO"];
export type Preview = components["schemas"]["PreviewDTO"];
export type Impact = components["schemas"]["ImpactDTO"];
export type UploadResult = components["schemas"]["UploadDTO"];
export class ApiError extends Error {
  constructor(
    public code: string,
    message: string,
    public status: number,
    public details: Record<string, unknown> = {},
  ) {
    super(message);
  }
}
let csrf = "";
export function setCsrf(value: string) {
  csrf = value;
}
export async function api<T>(
  path: string,
  options: { method?: string; body?: unknown; key?: string } = {},
): Promise<T> {
  const method = options.method ?? "GET";
  const headers: Record<string, string> = {};
  const form = options.body instanceof FormData;
  if (options.body !== undefined && !form)
    headers["Content-Type"] = "application/json";
  if (method !== "GET") headers["X-CSRF-Token"] = csrf;
  if (options.key) headers["Idempotency-Key"] = options.key;
  const init: RequestInit = {
    method,
    credentials: "same-origin",
    headers,
    body:
      options.body === undefined
        ? undefined
        : form
          ? (options.body as FormData)
          : JSON.stringify(options.body),
  };
  let response: Response;
  try {
    response = await fetch("/api/v1" + path, init);
  } catch {
    if (!options.key)
      throw new ApiError(
        "NETWORK_ERROR",
        "连接中断，操作状态待确认。请检查网络。",
        0,
      );
    try {
      response = await fetch("/api/v1" + path, init);
    } catch {
      throw new ApiError(
        "NETWORK_ERROR",
        "连接中断，操作状态待确认。请检查网络后重试。",
        0,
      );
    }
  }
  const result = await response.json();
  if (!response.ok) {
    if (response.status === 401 && path !== "/me" && !path.startsWith("/auth/"))
      window.dispatchEvent(new Event("study-trail:unauthorized"));
    throw new ApiError(
      result.error?.code ?? "SERVICE_UNAVAILABLE",
      result.error?.message ?? "服务暂时不可用",
      response.status,
      result.error?.details,
    );
  }
  return result.data as T;
}
export async function all<T>(path: string): Promise<T[]> {
  const items: T[] = [];
  let cursor: string | null = null;
  do {
    const page: { items: T[]; next_cursor: string | null } = await api(
      path +
        (path.includes("?") ? "&" : "?") +
        "limit=100" +
        (cursor ? "&cursor=" + encodeURIComponent(cursor) : ""),
    );
    items.push(...page.items);
    cursor = page.next_cursor;
  } while (cursor);
  return items;
}
export const mutation = <T>(path: string, body: unknown = {}, key?: string) =>
  api<T>(path, { method: "POST", body, key });
export const patch = <T>(path: string, body: unknown) =>
  api<T>(path, { method: "PATCH", body });
export const remove = (path: string) =>
  api<{ deleted_id: string }>(path, { method: "DELETE" });
export const operationKey = () => crypto.randomUUID();
export function subscribe(
  taskId: string,
  onUpdate: (task: Task) => void,
  onStatus: (connected: boolean) => void,
) {
  let stream: EventSource | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let revision = 0;
  let closed = false;
  let failures = 0;
  function connect() {
    if (closed) return;
    if (!navigator.onLine) {
      onStatus(false);
      return;
    }
    const current = new EventSource(
      `/api/v1/tasks/${taskId}/events?after_revision=${revision}`,
    );
    stream = current;
    current.onopen = () => {
      if (closed || stream !== current) return;
      failures = 0;
      onStatus(true);
    };
    for (const event of [
      "snapshot",
      "progress",
      "completed",
      "failed",
      "stopped",
    ])
      current.addEventListener(event, (raw) => {
        if (closed || stream !== current) return;
        const task = JSON.parse((raw as MessageEvent).data).data as Task;
        if (task.revision >= revision) {
          revision = task.revision;
          onUpdate(task);
        }
        if (["succeeded", "failed", "stopped"].includes(task.status)) close();
      });
    current.onerror = async () => {
      if (closed || stream !== current) return;
      current.close();
      onStatus(false);
      try {
        const task = await api<Task>(`/tasks/${taskId}`);
        if (closed || stream !== current) return;
        if (task.revision >= revision) {
          revision = task.revision;
          onUpdate(task);
        }
        if (["succeeded", "failed", "stopped"].includes(task.status)) {
          close();
          return;
        }
      } catch (error) {
        if (error instanceof ApiError && [401, 404].includes(error.status)) {
          close();
          return;
        }
      }
      if (!closed)
        timer = setTimeout(
          connect,
          [1000, 2000, 4000, 8000, 15000][Math.min(failures++, 4)],
        );
    };
  }
  function offline() {
    if (closed) return;
    stream?.close();
    stream = null;
    if (timer) clearTimeout(timer);
    onStatus(false);
  }
  function online() {
    if (!closed) {
      if (timer) clearTimeout(timer);
      stream?.close();
      connect();
    }
  }
  function close() {
    closed = true;
    stream?.close();
    if (timer) clearTimeout(timer);
    window.removeEventListener("online", online);
    window.removeEventListener("offline", offline);
  }
  window.addEventListener("online", online);
  window.addEventListener("offline", offline);
  connect();
  return close;
}
