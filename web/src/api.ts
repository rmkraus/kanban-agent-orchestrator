import type { AgentEndpoint, BoardStats, Comment, Runner, RunnerCreateResult, Snapshot, Task, TaskDetail } from "./types";

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options.headers,
    },
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

export function getSnapshot(): Promise<Snapshot> {
  return request<Snapshot>("/api/v1/snapshot");
}

export function getStats(): Promise<BoardStats> {
  return request<BoardStats>("/api/v1/stats");
}

export function getTaskDetail(taskId: string): Promise<TaskDetail> {
  return request<TaskDetail>(`/api/v1/tasks/${taskId}`);
}

export function createRunner(payload: { name: string; enabled?: boolean }): Promise<RunnerCreateResult> {
  return request<RunnerCreateResult>("/api/v1/runners", { method: "POST", body: JSON.stringify(payload) });
}

export function updateRunner(runnerId: string, payload: { name?: string; enabled?: boolean }): Promise<Runner> {
  return request<Runner>(`/api/v1/runners/${runnerId}`, { method: "PATCH", body: JSON.stringify(payload) });
}

export function deleteRunner(runnerId: string): Promise<void> {
  return request<void>(`/api/v1/runners/${runnerId}`, { method: "DELETE" });
}

export function createEndpoint(payload: { name: string; max_concurrency: number; enabled?: boolean; runner_id?: string | null }): Promise<AgentEndpoint> {
  return request<AgentEndpoint>("/api/v1/agent-endpoints", { method: "POST", body: JSON.stringify(payload) });
}

export function updateEndpoint(
  endpointId: string,
  payload: { name?: string; max_concurrency?: number; enabled?: boolean; runner_id?: string | null },
): Promise<AgentEndpoint> {
  return request<AgentEndpoint>(`/api/v1/agent-endpoints/${endpointId}`, { method: "PATCH", body: JSON.stringify(payload) });
}

export function createTask(payload: { title: string; body: string; agent_endpoint_id: string; priority: number; exclusive: boolean }): Promise<Task> {
  return request<Task>("/api/v1/tasks", { method: "POST", body: JSON.stringify(payload) });
}

export function updateTask(
  taskId: string,
  payload: { title?: string; body?: string; agent_endpoint_id?: string; priority?: number; exclusive?: boolean },
): Promise<Task> {
  return request<Task>(`/api/v1/tasks/${taskId}`, { method: "PATCH", body: JSON.stringify(payload) });
}

export function addComment(taskId: string, payload: { body: string; author: string }): Promise<Comment> {
  return request<Comment>(`/api/v1/tasks/${taskId}/comments`, { method: "POST", body: JSON.stringify(payload) });
}

export function unblockTask(taskId: string): Promise<Task> {
  return request<Task>(`/api/v1/tasks/${taskId}/unblock`, { method: "POST" });
}
