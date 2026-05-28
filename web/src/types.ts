export type TaskStatus = "todo" | "ready" | "running" | "blocked" | "done" | "archived";
export type RunStatus = "leased" | "running" | "completed" | "failed" | "blocked";
export type QuestionStatus = "open" | "answered";

export interface Runner {
  id: string;
  name: string;
  enabled: boolean;
  created_at: string;
  last_seen_at: string | null;
}

export interface RunnerCreateResult {
  runner: Runner;
  psk: string;
}

export interface AgentEndpoint {
  id: string;
  name: string;
  max_concurrency: number;
  enabled: boolean;
  created_at: string;
  runner_id: string | null;
}

export interface Task {
  id: string;
  title: string;
  body: string;
  agent_endpoint_id: string;
  status: TaskStatus;
  priority: number;
  exclusive: boolean;
  parent_ids: string[];
  child_ids: string[];
  created_at: string;
  updated_at: string;
}

export interface Run {
  id: string;
  task_id: string;
  runner_id: string;
  agent_endpoint_id: string;
  status: RunStatus;
  leased_until: string;
  started_at: string;
  heartbeat_at: string;
  finished_at: string | null;
  summary: string | null;
}

export interface Comment {
  id: string;
  task_id: string;
  author: string;
  body: string;
  created_at: string;
}

export interface Question {
  id: string;
  task_id: string;
  run_id: string | null;
  asked_by: string;
  body: string;
  status: QuestionStatus;
  resolves_block: boolean;
  answer_body: string | null;
  answered_by: string | null;
  created_at: string;
  answered_at: string | null;
}

export interface Artifact {
  id: string;
  task_id: string;
  filename: string;
  content_markdown: string;
  description: string;
  created_at: string;
}

export interface Event {
  id: number;
  kind: string;
  message: string;
  task_id: string | null;
  run_id: string | null;
  created_at: string;
  metadata: Record<string, string | number | boolean | null>;
}

export interface Snapshot {
  runners: Runner[];
  agent_endpoints: AgentEndpoint[];
  tasks: Task[];
  runs: Run[];
  events: Event[];
  comments: Comment[];
  questions: Question[];
  artifacts: Artifact[];
  next_event_id: number;
}

export interface TaskDetail {
  task: Task;
  parents: Task[];
  children: Task[];
  runs: Run[];
  comments: Comment[];
  questions: Question[];
  artifacts: Artifact[];
  events: Event[];
}

export interface BoardStats {
  total_tasks: number;
  by_status: Record<TaskStatus, number>;
  active_runs: number;
  ready_tasks: number;
  blocked_tasks: number;
}
