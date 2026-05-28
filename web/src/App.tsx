import { FormEvent, useEffect, useMemo, useState } from "react";

import { addComment, answerQuestion, createEndpoint, createTask, getSnapshot, getStats, getTaskDetail, unblockTask } from "./api";
import type { AgentEndpoint, BoardStats, Snapshot, Task, TaskDetail, TaskStatus } from "./types";

const statuses: TaskStatus[] = ["todo", "ready", "running", "blocked", "done"];

function endpointName(endpoints: AgentEndpoint[], id: string): string {
  return endpoints.find((endpoint) => endpoint.id === id)?.name ?? id.slice(0, 8);
}

function formatDate(value: string | null): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(new Date(value));
}

export function App() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [stats, setStats] = useState<BoardStats | null>(null);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const selectedTask = useMemo(() => snapshot?.tasks.find((task) => task.id === selectedTaskId) ?? null, [snapshot, selectedTaskId]);

  async function refresh(nextSelectedTaskId = selectedTaskId) {
    setError(null);
    setIsLoading(true);
    try {
      const [nextSnapshot, nextStats] = await Promise.all([getSnapshot(), getStats()]);
      setSnapshot(nextSnapshot);
      setStats(nextStats);
      const taskId = nextSelectedTaskId ?? nextSnapshot.tasks[0]?.id ?? null;
      setSelectedTaskId(taskId);
      setDetail(taskId ? await getTaskDetail(taskId) : null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setIsLoading(false);
    }
  }

  async function selectTask(task: Task) {
    setSelectedTaskId(task.id);
    setDetail(await getTaskDetail(task.id));
  }

  useEffect(() => {
    void refresh(null);
  }, []);

  const tasksByStatus = useMemo(() => {
    const grouped = Object.fromEntries(statuses.map((status) => [status, [] as Task[]])) as Record<TaskStatus, Task[]>;
    for (const task of snapshot?.tasks ?? []) {
      if (task.status in grouped) grouped[task.status].push(task);
    }
    return grouped;
  }, [snapshot]);

  return (
    <div className="app-shell">
      <header className="topbar">
        <div>
          <div className="eyebrow">Agent orchestration</div>
          <h1>Kanban</h1>
        </div>
        <div className="topbar-actions">
          {stats && (
            <div className="stats-strip">
              <span>{stats.total_tasks} tasks</span>
              <span>{stats.active_runs} running</span>
              <span>{stats.blocked_tasks} blocked</span>
            </div>
          )}
          <button className="secondary" onClick={() => void refresh()} disabled={isLoading}>
            {isLoading ? "Refreshing" : "Refresh"}
          </button>
        </div>
      </header>

      {error && <div className="error-banner">{error}</div>}

      <main className="workspace">
        <aside className="sidebar">
          <EndpointPanel endpoints={snapshot?.agent_endpoints ?? []} onChange={() => void refresh()} />
          <CreateTaskPanel endpoints={snapshot?.agent_endpoints ?? []} onChange={(taskId) => void refresh(taskId)} />
        </aside>

        <section className="board" aria-label="Task board">
          {statuses.map((status) => (
            <section className="column" key={status}>
              <div className="column-header">
                <h2>{status}</h2>
                <span>{tasksByStatus[status].length}</span>
              </div>
              <div className="cards">
                {tasksByStatus[status].map((task) => (
                  <button
                    className={`task-card ${selectedTaskId === task.id ? "selected" : ""} ${task.status}`}
                    key={task.id}
                    onClick={() => void selectTask(task)}
                  >
                    <div className="card-title">{task.title}</div>
                    <div className="card-meta">{endpointName(snapshot?.agent_endpoints ?? [], task.agent_endpoint_id)}</div>
                    {task.body && <p>{task.body}</p>}
                    <div className="badges">
                      <span>p{task.priority}</span>
                      <span>{task.parent_ids.length} parents</span>
                      <span>{task.child_ids.length} children</span>
                      {snapshot?.questions.some((question) => question.task_id === task.id && question.status === "open") && (
                        <span className="blocked-badge">question</span>
                      )}
                    </div>
                  </button>
                ))}
              </div>
            </section>
          ))}
        </section>

        <TaskPanel detail={detail} selectedTask={selectedTask} endpoints={snapshot?.agent_endpoints ?? []} onChange={() => void refresh(selectedTaskId)} />
      </main>
    </div>
  );
}

function EndpointPanel({ endpoints, onChange }: { endpoints: AgentEndpoint[]; onChange: () => void }) {
  const [name, setName] = useState("");
  const [maxConcurrency, setMaxConcurrency] = useState(1);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!name.trim()) return;
    await createEndpoint({ name: name.trim(), max_concurrency: maxConcurrency });
    setName("");
    onChange();
  }

  return (
    <section className="panel">
      <div className="panel-heading">
        <h2>Endpoints</h2>
        <span>{endpoints.length}</span>
      </div>
      <div className="endpoint-list">
        {endpoints.map((endpoint) => (
          <div className="endpoint-row" key={endpoint.id}>
            <span>{endpoint.name}</span>
            <small>{endpoint.max_concurrency} max</small>
          </div>
        ))}
      </div>
      <form onSubmit={(event) => void submit(event)} className="stacked-form">
        <label>
          Name
          <input value={name} onChange={(event) => setName(event.target.value)} placeholder="coder" />
        </label>
        <label>
          Max concurrency
          <input type="number" min="1" value={maxConcurrency} onChange={(event) => setMaxConcurrency(Number(event.target.value || 1))} />
        </label>
        <button type="submit">Create endpoint</button>
      </form>
    </section>
  );
}

function CreateTaskPanel({ endpoints, onChange }: { endpoints: AgentEndpoint[]; onChange: (taskId: string) => void }) {
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [endpointId, setEndpointId] = useState("");
  const [priority, setPriority] = useState(0);
  const [exclusive, setExclusive] = useState(false);

  const activeEndpointId = endpointId || endpoints[0]?.id || "";

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!title.trim() || !activeEndpointId) return;
    const task = await createTask({ title: title.trim(), body, agent_endpoint_id: activeEndpointId, priority, exclusive });
    setTitle("");
    setBody("");
    setPriority(0);
    setExclusive(false);
    onChange(task.id);
  }

  return (
    <section className="panel">
      <div className="panel-heading">
        <h2>Create Task</h2>
      </div>
      <form onSubmit={(event) => void submit(event)} className="stacked-form">
        <label>
          Endpoint
          <select value={activeEndpointId} onChange={(event) => setEndpointId(event.target.value)}>
            {endpoints.map((endpoint) => (
              <option value={endpoint.id} key={endpoint.id}>
                {endpoint.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Title
          <input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Ship useful work" />
        </label>
        <label>
          Body
          <textarea value={body} onChange={(event) => setBody(event.target.value)} rows={4} />
        </label>
        <label>
          Priority
          <input type="number" value={priority} onChange={(event) => setPriority(Number(event.target.value || 0))} />
        </label>
        <label className="checkbox-row">
          <input type="checkbox" checked={exclusive} onChange={(event) => setExclusive(event.target.checked)} /> Exclusive
        </label>
        <button type="submit" disabled={!activeEndpointId}>
          Create task
        </button>
      </form>
    </section>
  );
}

function TaskPanel({
  detail,
  selectedTask,
  endpoints,
  onChange,
}: {
  detail: TaskDetail | null;
  selectedTask: Task | null;
  endpoints: AgentEndpoint[];
  onChange: () => void;
}) {
  const [comment, setComment] = useState("");
  const [answerByQuestion, setAnswerByQuestion] = useState<Record<string, string>>({});

  if (!selectedTask || !detail) {
    return (
      <aside className="detail-panel empty">
        <p>Select a task to inspect the thread.</p>
      </aside>
    );
  }

  async function submitComment(event: FormEvent) {
    event.preventDefault();
    if (!comment.trim()) return;
    await addComment(selectedTask!.id, { body: comment.trim(), author: "human" });
    setComment("");
    onChange();
  }

  async function submitAnswer(questionId: string) {
    const body = answerByQuestion[questionId]?.trim();
    if (!body || !selectedTask) return;
    await answerQuestion(selectedTask.id, questionId, { body, answered_by: "human", unblock_if_resolved: true });
    setAnswerByQuestion((answers) => ({ ...answers, [questionId]: "" }));
    onChange();
  }

  return (
    <aside className="detail-panel">
      <div className="detail-header">
        <div>
          <div className="eyebrow">{endpointName(endpoints, selectedTask.agent_endpoint_id)}</div>
          <h2>{selectedTask.title}</h2>
        </div>
        <span className={`status-pill ${selectedTask.status}`}>{selectedTask.status}</span>
      </div>
      {selectedTask.body && <p className="task-body">{selectedTask.body}</p>}

      <dl className="meta-grid">
        <div>
          <dt>Priority</dt>
          <dd>{selectedTask.priority}</dd>
        </div>
        <div>
          <dt>Updated</dt>
          <dd>{formatDate(selectedTask.updated_at)}</dd>
        </div>
        <div>
          <dt>Parents</dt>
          <dd>{detail.parents.length}</dd>
        </div>
        <div>
          <dt>Children</dt>
          <dd>{detail.children.length}</dd>
        </div>
      </dl>

      {selectedTask.status === "blocked" && (
        <button className="secondary full-width" onClick={() => void unblockTask(selectedTask.id).then(onChange)}>
          Unblock manually
        </button>
      )}

      <section className="thread-section">
        <h3>Questions</h3>
        {detail.questions.length === 0 && <p className="muted">No questions yet.</p>}
        {detail.questions.map((question) => (
          <article className="question" key={question.id}>
            <div className="question-topline">
              <strong>{question.asked_by}</strong>
              <span className={`status-pill small ${question.status}`}>{question.status}</span>
            </div>
            <p>{question.body}</p>
            {question.answer_body ? (
              <div className="answer-box">
                <strong>{question.answered_by}</strong>
                <p>{question.answer_body}</p>
              </div>
            ) : (
              <div className="answer-form">
                <textarea
                  value={answerByQuestion[question.id] ?? ""}
                  onChange={(event) => setAnswerByQuestion((answers) => ({ ...answers, [question.id]: event.target.value }))}
                  placeholder="Answer and resolve…"
                  rows={3}
                />
                <button onClick={() => void submitAnswer(question.id)}>Answer</button>
              </div>
            )}
          </article>
        ))}
      </section>

      <section className="thread-section">
        <h3>Chat</h3>
        <div className="comments">
          {detail.comments.map((entry) => (
            <article className="comment" key={entry.id}>
              <div>
                <strong>{entry.author}</strong>
                <span>{formatDate(entry.created_at)}</span>
              </div>
              <p>{entry.body}</p>
            </article>
          ))}
        </div>
        <form onSubmit={(event) => void submitComment(event)} className="comment-form">
          <textarea value={comment} onChange={(event) => setComment(event.target.value)} rows={3} placeholder="Add a task note…" />
          <button type="submit">Send</button>
        </form>
      </section>
    </aside>
  );
}
