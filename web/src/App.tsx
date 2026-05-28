import { FormEvent, useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";

import {
  addComment,
  createEndpoint,
  createRunner,
  createTask,
  deleteRunner,
  getSnapshot,
  getStats,
  getTaskDetail,
  unblockTask,
  updateEndpoint,
  updateRunner,
  updateTask,
} from "./api";
import type { AgentEndpoint, BoardStats, Runner, RunnerCreateResult, Snapshot, Task, TaskDetail, TaskStatus } from "./types";

const statuses: TaskStatus[] = ["todo", "ready", "running", "blocked", "done"];

function endpointName(endpoints: AgentEndpoint[], id: string): string {
  return endpoints.find((endpoint) => endpoint.id === id)?.name ?? id.slice(0, 8);
}

function runnerName(runners: Runner[], id: string | null): string {
  if (!id) return "Unassigned";
  return runners.find((runner) => runner.id === id)?.name ?? id.slice(0, 8);
}

function formatDate(value: string | null): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(new Date(value));
}

function safeUnitName(name: string): string {
  return `kanban-runner-${
    name
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "") || "runner"
  }`;
}

function runnerOrigin(): string {
  if (typeof window === "undefined") return "http://127.0.0.1:8082";
  const url = new URL(window.location.origin);
  url.port = "8082";
  return url.origin;
}

function shortId(id: string): string {
  return id.slice(0, 8);
}

export function App() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [stats, setStats] = useState<BoardStats | null>(null);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isCreateTaskOpen, setIsCreateTaskOpen] = useState(false);
  const [isRunnersOpen, setIsRunnersOpen] = useState(false);
  const [isBackendsOpen, setIsBackendsOpen] = useState(false);
  const [isMainMenuOpen, setIsMainMenuOpen] = useState(false);

  const selectedTask = useMemo(() => snapshot?.tasks.find((task) => task.id === selectedTaskId) ?? null, [snapshot, selectedTaskId]);

  async function refresh(nextSelectedTaskId = selectedTaskId) {
    setError(null);
    try {
      const [nextSnapshot, nextStats] = await Promise.all([getSnapshot(), getStats()]);
      setSnapshot(nextSnapshot);
      setStats(nextStats);
      const taskId = nextSelectedTaskId && nextSnapshot.tasks.some((task) => task.id === nextSelectedTaskId) ? nextSelectedTaskId : null;
      setSelectedTaskId(taskId);
      setDetail(taskId ? await getTaskDetail(taskId) : null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }

  async function selectTask(task: Task) {
    setSelectedTaskId(task.id);
    setDetail(await getTaskDetail(task.id));
  }

  useEffect(() => {
    void refresh(selectedTaskId);
    const intervalId = window.setInterval(() => void refresh(selectedTaskId), 3000);
    return () => window.clearInterval(intervalId);
  }, [selectedTaskId]);

  const tasksByStatus = useMemo(() => {
    const grouped = Object.fromEntries(statuses.map((status) => [status, [] as Task[]])) as Record<TaskStatus, Task[]>;
    for (const task of snapshot?.tasks ?? []) {
      if (task.status in grouped) grouped[task.status].push(task);
    }
    return grouped;
  }, [snapshot]);

  const runners = snapshot?.runners ?? [];
  const backends = snapshot?.agent_endpoints ?? [];

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="title-block">
          <div className="eyebrow">Agent orchestration</div>
          <div className="title-row">
            <div className="topbar-actions">
              <button
                className="secondary menu-button"
                type="button"
                aria-label="Open menu"
                aria-expanded={isMainMenuOpen}
                onClick={() => setIsMainMenuOpen((open) => !open)}
              >
                <span className="hamburger-icon" aria-hidden="true">
                  <span />
                  <span />
                  <span />
                </span>
              </button>
              {isMainMenuOpen && (
                <div className="main-menu" role="menu">
                  {stats && (
                    <div className="stats-strip" aria-label="Board status">
                      <span>{stats.total_tasks} tasks</span>
                      <span>{stats.active_runs} running</span>
                      <span>{stats.blocked_tasks} blocked</span>
                    </div>
                  )}
                  <button
                    className="secondary"
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      setIsMainMenuOpen(false);
                      setIsRunnersOpen(true);
                    }}
                  >
                    Runners
                  </button>
                  <button
                    className="secondary"
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      setIsMainMenuOpen(false);
                      setIsBackendsOpen(true);
                    }}
                  >
                    Backends
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      setIsMainMenuOpen(false);
                      setIsCreateTaskOpen(true);
                    }}
                  >
                    Create task
                  </button>
                </div>
              )}
            </div>
            <h1>Kanban</h1>
          </div>
        </div>
      </header>

      {error && <div className="error-banner">{error}</div>}

      <main className="workspace board-only">
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
                    <div className="card-meta">{endpointName(backends, task.agent_endpoint_id)}</div>
                    <div className="badges">
                      <span title={task.id}>id {shortId(task.id)}</span>
                      <span title={task.context_id}>ctx {shortId(task.context_id)}</span>
                      <span>p{task.priority}</span>
                      <span>{task.parent_ids.length} parents</span>
                      <span>{task.child_ids.length} children</span>
                    </div>
                  </button>
                ))}
              </div>
            </section>
          ))}
        </section>
      </main>

      <TaskPanel
        detail={detail}
        selectedTask={selectedTask}
        endpoints={backends}
        onChange={() => void refresh(selectedTaskId)}
        onClose={() => {
          setSelectedTaskId(null);
          setDetail(null);
        }}
      />
      {isCreateTaskOpen && (
        <CreateTaskModal
          endpoints={backends}
          onClose={() => setIsCreateTaskOpen(false)}
          onChange={(taskId) => {
            setIsCreateTaskOpen(false);
            void refresh(taskId);
          }}
        />
      )}
      {isRunnersOpen && <RunnersModal runners={runners} onClose={() => setIsRunnersOpen(false)} onChange={() => void refresh()} />}
      {isBackendsOpen && <BackendsModal endpoints={backends} runners={runners} onClose={() => setIsBackendsOpen(false)} onChange={() => void refresh()} />}
    </div>
  );
}

function RunnersModal({ runners, onChange, onClose }: { runners: Runner[]; onChange: () => void; onClose: () => void }) {
  const [name, setName] = useState("");
  const [created, setCreated] = useState<RunnerCreateResult | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!name.trim()) return;
    const result = await createRunner({ name: name.trim() });
    setCreated(result);
    setName("");
    onChange();
  }

  async function removeRunner(runner: Runner) {
    if (!window.confirm(`Delete runner "${runner.name}"? Backends assigned to it will be disabled and unassigned.`)) return;
    await deleteRunner(runner.id);
    if (created?.runner.id === runner.id) setCreated(null);
    onChange();
  }

  const origin = runnerOrigin();
  const unitName = safeUnitName(created?.runner.name ?? "runner");
  const foregroundCommand = created ? `KANBAN_PSK='${created.psk}' kanban-runner run --server '${origin}' --runner-id '${created.runner.id}'` : "";
  const systemdCommand = created
    ? `printf '%s' '${created.psk}' | sudo kanban-runner install-systemd --server '${origin}' --runner-id '${created.runner.id}' --name '${unitName}' --psk-stdin`
    : "";

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <section
        className="detail-panel admin-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="runners-title"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="detail-header">
          <div>
            <div className="eyebrow">Remote workers</div>
            <h2 id="runners-title">Runners</h2>
          </div>
          <button className="icon-button" type="button" aria-label="Close runners" onClick={onClose}>
            ×
          </button>
        </div>

        <form onSubmit={(event) => void submit(event)} className="inline-form">
          <label>
            Runner name
            <input value={name} onChange={(event) => setName(event.target.value)} placeholder="jetson" />
          </label>
          <button type="submit">Create runner</button>
        </form>

        {created && (
          <section className="command-card">
            <h3>Copy/paste registration</h3>
            <p className="muted">The PSK is shown once. Save this command now or rotate later. Security theater avoided, barely.</p>
            <label>
              Foreground test
              <textarea readOnly rows={3} value={foregroundCommand} />
            </label>
            <label>
              Install as systemd service
              <textarea readOnly rows={4} value={systemdCommand} />
            </label>
          </section>
        )}

        <div className="admin-list">
          {runners.map((runner) => (
            <div className="admin-row" key={runner.id}>
              <div>
                <strong>{runner.name}</strong>
                <small>Last seen: {formatDate(runner.last_seen_at)}</small>
              </div>
              <div className="row-actions">
                <button
                  className={runner.enabled ? "secondary" : ""}
                  type="button"
                  onClick={() => void updateRunner(runner.id, { enabled: !runner.enabled }).then(onChange)}
                >
                  {runner.enabled ? "Disable" : "Enable"}
                </button>
                <button className="danger" type="button" onClick={() => void removeRunner(runner)}>
                  Delete
                </button>
              </div>
            </div>
          ))}
          {runners.length === 0 && <p className="muted">No runners yet. Create one to get the install command.</p>}
        </div>
      </section>
    </div>
  );
}

function BackendsModal({
  endpoints,
  runners,
  onChange,
  onClose,
}: {
  endpoints: AgentEndpoint[];
  runners: Runner[];
  onChange: () => void;
  onClose: () => void;
}) {
  const [name, setName] = useState("");
  const [runnerId, setRunnerId] = useState("");
  const [maxConcurrency, setMaxConcurrency] = useState(1);
  const activeRunnerId = runnerId || runners[0]?.id || "";

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!name.trim() || !activeRunnerId) return;
    await createEndpoint({ name: name.trim(), max_concurrency: maxConcurrency, runner_id: activeRunnerId });
    setName("");
    setMaxConcurrency(1);
    onChange();
  }

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <section
        className="detail-panel admin-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="backends-title"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="detail-header">
          <div>
            <div className="eyebrow">Task targets</div>
            <h2 id="backends-title">Backends</h2>
          </div>
          <button className="icon-button" type="button" aria-label="Close backends" onClick={onClose}>
            ×
          </button>
        </div>

        <form onSubmit={(event) => void submit(event)} className="stacked-form task-form">
          <label>
            Backend name
            <input value={name} onChange={(event) => setName(event.target.value)} placeholder="coder" />
          </label>
          <label>
            Runner
            <select value={activeRunnerId} onChange={(event) => setRunnerId(event.target.value)} disabled={runners.length === 0}>
              {runners.map((runner) => (
                <option value={runner.id} key={runner.id}>
                  {runner.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Max concurrency
            <input type="number" min="1" value={maxConcurrency} onChange={(event) => setMaxConcurrency(Number(event.target.value || 1))} />
          </label>
          <button type="submit" disabled={!activeRunnerId}>
            Create backend
          </button>
        </form>

        <div className="admin-list">
          {endpoints.map((endpoint) => (
            <div className="admin-row" key={endpoint.id}>
              <div>
                <strong>{endpoint.name}</strong>
                <small>
                  {runnerName(runners, endpoint.runner_id)} · {endpoint.max_concurrency} max
                </small>
              </div>
              <div className="row-actions">
                <select
                  value={endpoint.runner_id ?? ""}
                  onChange={(event) => void updateEndpoint(endpoint.id, { runner_id: event.target.value }).then(onChange)}
                >
                  <option value="" disabled>
                    Assign runner
                  </option>
                  {runners.map((runner) => (
                    <option value={runner.id} key={runner.id}>
                      {runner.name}
                    </option>
                  ))}
                </select>
                <button
                  className={endpoint.enabled ? "secondary" : ""}
                  type="button"
                  onClick={() => void updateEndpoint(endpoint.id, { enabled: !endpoint.enabled }).then(onChange)}
                >
                  {endpoint.enabled ? "Disable" : "Enable"}
                </button>
              </div>
            </div>
          ))}
          {endpoints.length === 0 && <p className="muted">No backends yet. Create a runner first, then attach a backend to it.</p>}
        </div>
      </section>
    </div>
  );
}

function CreateTaskModal({ endpoints, onChange, onClose }: { endpoints: AgentEndpoint[]; onChange: (taskId: string) => void; onClose: () => void }) {
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [endpointId, setEndpointId] = useState("");
  const [priority, setPriority] = useState(0);
  const [exclusive, setExclusive] = useState(false);

  const enabledEndpoints = endpoints.filter((endpoint) => endpoint.enabled && endpoint.runner_id);
  const activeEndpointId = endpointId || enabledEndpoints[0]?.id || "";

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
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <section
        className="detail-panel task-form-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-task-title"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="detail-header">
          <div>
            <div className="eyebrow">New work item</div>
            <h2 id="create-task-title">Create Task</h2>
          </div>
          <button className="icon-button" type="button" aria-label="Close create task" onClick={onClose}>
            ×
          </button>
        </div>
        <form onSubmit={(event) => void submit(event)} className="stacked-form task-form">
          <label>
            Backend
            <select value={activeEndpointId} onChange={(event) => setEndpointId(event.target.value)} disabled={enabledEndpoints.length === 0}>
              {enabledEndpoints.map((endpoint) => (
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
    </div>
  );
}

function TaskPanel({
  detail,
  selectedTask,
  endpoints,
  onChange,
  onClose,
}: {
  detail: TaskDetail | null;
  selectedTask: Task | null;
  endpoints: AgentEndpoint[];
  onChange: () => void;
  onClose: () => void;
}) {
  const [comment, setComment] = useState("");
  const [isEditing, setIsEditing] = useState(false);
  const [editTitle, setEditTitle] = useState(selectedTask?.title ?? "");
  const [editBody, setEditBody] = useState(selectedTask?.body ?? "");
  const [editEndpointId, setEditEndpointId] = useState(selectedTask?.agent_endpoint_id ?? "");
  const [editPriority, setEditPriority] = useState(selectedTask?.priority ?? 0);
  const [editExclusive, setEditExclusive] = useState(selectedTask?.exclusive ?? false);

  useEffect(() => {
    setIsEditing(false);
    setEditTitle(selectedTask?.title ?? "");
    setEditBody(selectedTask?.body ?? "");
    setEditEndpointId(selectedTask?.agent_endpoint_id ?? "");
    setEditPriority(selectedTask?.priority ?? 0);
    setEditExclusive(selectedTask?.exclusive ?? false);
  }, [selectedTask?.id]);

  if (!selectedTask || !detail) {
    return null;
  }

  async function submitComment(event: FormEvent) {
    event.preventDefault();
    if (!comment.trim()) return;
    await addComment(selectedTask!.id, { body: comment.trim(), author: "human" });
    setComment("");
    onChange();
  }

  async function submitEdit(event: FormEvent) {
    event.preventDefault();
    if (!editTitle.trim() || !editEndpointId) return;
    await updateTask(selectedTask!.id, {
      title: editTitle.trim(),
      body: editBody,
      agent_endpoint_id: editEndpointId,
      priority: editPriority,
      exclusive: editExclusive,
    });
    setIsEditing(false);
    onChange();
  }

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <aside className="detail-panel" role="dialog" aria-modal="true" aria-labelledby="task-detail-title" onClick={(event) => event.stopPropagation()}>
        <div className="detail-header">
          <div>
            <div className="eyebrow">{endpointName(endpoints, selectedTask.agent_endpoint_id)}</div>
            <h2 id="task-detail-title">{selectedTask.title}</h2>
          </div>
          <div className="detail-actions">
            <button className="secondary" type="button" onClick={() => setIsEditing((editing) => !editing)}>
              {isEditing ? "Cancel" : "Edit"}
            </button>
            <span className={`status-pill ${selectedTask.status}`}>{selectedTask.status}</span>
            <button className="icon-button" type="button" aria-label="Close task detail" onClick={onClose}>
              ×
            </button>
          </div>
        </div>

        {isEditing ? (
          <form onSubmit={(event) => void submitEdit(event)} className="stacked-form edit-task-form">
            <label>
              Title
              <input value={editTitle} onChange={(event) => setEditTitle(event.target.value)} />
            </label>
            <label>
              Backend
              <select value={editEndpointId} onChange={(event) => setEditEndpointId(event.target.value)}>
                {endpoints.map((endpoint) => (
                  <option value={endpoint.id} key={endpoint.id}>
                    {endpoint.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Body (Markdown)
              <textarea value={editBody} onChange={(event) => setEditBody(event.target.value)} rows={8} />
            </label>
            <label>
              Priority
              <input type="number" value={editPriority} onChange={(event) => setEditPriority(Number(event.target.value || 0))} />
            </label>
            <label className="checkbox-row">
              <input type="checkbox" checked={editExclusive} onChange={(event) => setEditExclusive(event.target.checked)} /> Exclusive
            </label>
            <button type="submit">Save changes</button>
          </form>
        ) : (
          selectedTask.body && (
            <section className="markdown-body task-body">
              <ReactMarkdown>{selectedTask.body}</ReactMarkdown>
            </section>
          )
        )}

        <dl className="meta-grid">
          <div>
            <dt>Priority</dt>
            <dd>{selectedTask.priority}</dd>
          </div>
          <div>
            <dt>ID</dt>
            <dd title={selectedTask.id}> {shortId(selectedTask.id)}</dd>
          </div>
          <div>
            <dt>Context</dt>
            <dd title={selectedTask.context_id}> {shortId(selectedTask.context_id)}</dd>
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
          <h3>Artifacts</h3>
          <div className="artifacts">
            {detail.artifacts.map((artifact) => (
              <article className="artifact" key={artifact.id}>
                <div>
                  <strong>{artifact.filename}</strong>
                  <span>{formatDate(artifact.created_at)}</span>
                </div>
                {artifact.description && <p className="muted">{artifact.description}</p>}
                <div className="markdown-body">
                  <ReactMarkdown>{artifact.content_markdown}</ReactMarkdown>
                </div>
              </article>
            ))}
            {detail.artifacts.length === 0 && <p className="muted">No artifacts yet.</p>}
          </div>
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
    </div>
  );
}
