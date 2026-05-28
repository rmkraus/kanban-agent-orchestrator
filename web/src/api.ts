import type { AgentEndpoint, BoardStats, Comment, Question, Snapshot, Task, TaskDetail } from "./types";

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

export function createEndpoint(payload: { name: string; max_concurrency: number; enabled?: boolean }): Promise<AgentEndpoint> {
  return request<AgentEndpoint>("/api/v1/agent-endpoints", { method: "POST", body: JSON.stringify(payload) });
}

export function createTask(payload: { title: string; body: string; agent_endpoint_id: string; priority: number; exclusive: boolean }): Promise<Task> {
  return request<Task>("/api/v1/tasks", { method: "POST", body: JSON.stringify(payload) });
}

export function addComment(taskId: string, payload: { body: string; author: string }): Promise<Comment> {
  return request<Comment>(`/api/v1/tasks/${taskId}/comments`, { method: "POST", body: JSON.stringify(payload) });
}

export function answerQuestion(
  taskId: string,
  questionId: string,
  payload: { body: string; answered_by: string; unblock_if_resolved: boolean },
): Promise<Question> {
  return request<Question>(`/api/v1/tasks/${taskId}/questions/${questionId}/answer`, { method: "POST", body: JSON.stringify(payload) });
}

export function unblockTask(taskId: string): Promise<Task> {
  return request<Task>(`/api/v1/tasks/${taskId}/unblock`, { method: "POST" });
}
