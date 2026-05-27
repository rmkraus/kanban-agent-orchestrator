from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from kanban_agent_orchestrator.errors import NotFoundError, OrchestratorError
from kanban_agent_orchestrator.kernel import OrchestratorKernel
from kanban_agent_orchestrator.models import AgentEndpoint, Artifact, Comment, Event, Lease, Run, Snapshot, Task, TaskDetail, TaskStatus


class AgentEndpointCreate(BaseModel):
    name: str
    max_concurrency: int = 1
    enabled: bool = True


class TaskCreate(BaseModel):
    title: str
    agent_endpoint_id: str
    body: str = ""
    priority: int = 0
    exclusive: bool = False


class DependencyCreate(BaseModel):
    parent_id: str
    child_id: str


class LeaseRequest(BaseModel):
    runner_id: str
    lease_seconds: int = 300


class RunFinishRequest(BaseModel):
    summary: str = ""


class RunFailRequest(BaseModel):
    summary: str = ""


class RunBlockRequest(BaseModel):
    reason: str


class HeartbeatRequest(BaseModel):
    lease_seconds: int = 300


class CommentCreate(BaseModel):
    body: str
    author: str = "system"


class ArtifactCreate(BaseModel):
    filename: str
    content_markdown: str
    description: str = ""


class BoardStats(BaseModel):
    total_tasks: int
    by_status: dict[str, int]
    active_runs: int
    ready_tasks: int
    blocked_tasks: int


def domain_error(error: OrchestratorError) -> HTTPException:
    status_code = 404 if isinstance(error, NotFoundError) else 400
    return HTTPException(status_code=status_code, detail=str(error))


def create_app(kernel: OrchestratorKernel | None = None) -> FastAPI:
    active_kernel = kernel or OrchestratorKernel.persistent()
    app = FastAPI(title="Kanban Agent Orchestrator", version="0.1.0")

    @app.get("/", response_class=HTMLResponse)
    def board() -> str:
        return BOARD_HTML

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/snapshot", response_model=Snapshot)
    def snapshot() -> Snapshot:
        active_kernel.reclaim_expired_leases()
        active_kernel.recompute_readiness()
        return active_kernel.snapshot()

    @app.get("/api/v1/stats", response_model=BoardStats)
    def stats() -> BoardStats:
        active_kernel.reclaim_expired_leases()
        active_kernel.recompute_readiness()
        by_status = {status.value: 0 for status in TaskStatus}
        for task in active_kernel.tasks.values():
            by_status[task.status.value] += 1
        active_runs = sum(1 for run in active_kernel.runs.values() if run.status in {"leased", "running"})
        return BoardStats(
            total_tasks=len(active_kernel.tasks),
            by_status=by_status,
            active_runs=active_runs,
            ready_tasks=by_status[TaskStatus.READY.value],
            blocked_tasks=by_status[TaskStatus.BLOCKED.value],
        )

    @app.get("/api/v1/agent-endpoints", response_model=list[AgentEndpoint])
    def list_agent_endpoints() -> list[AgentEndpoint]:
        return active_kernel.list_agent_endpoints()

    @app.post("/api/v1/agent-endpoints", response_model=AgentEndpoint)
    def create_agent_endpoint(payload: AgentEndpointCreate) -> AgentEndpoint:
        return active_kernel.create_agent_endpoint(name=payload.name, max_concurrency=payload.max_concurrency, enabled=payload.enabled)

    @app.get("/api/v1/tasks", response_model=list[Task])
    def list_tasks(status: TaskStatus | None = None) -> list[Task]:
        active_kernel.reclaim_expired_leases()
        active_kernel.recompute_readiness()
        return active_kernel.list_tasks(status=status)

    @app.post("/api/v1/tasks", response_model=Task)
    def create_task(payload: TaskCreate) -> Task:
        try:
            return active_kernel.create_task(
                title=payload.title,
                agent_endpoint_id=payload.agent_endpoint_id,
                body=payload.body,
                priority=payload.priority,
                exclusive=payload.exclusive,
            )
        except OrchestratorError as error:
            raise domain_error(error) from error

    @app.get("/api/v1/tasks/{task_id}", response_model=TaskDetail)
    def task_detail(task_id: str) -> TaskDetail:
        try:
            return active_kernel.task_detail(task_id)
        except OrchestratorError as error:
            raise domain_error(error) from error

    @app.post("/api/v1/tasks/{task_id}/unblock", response_model=Task)
    def unblock_task(task_id: str) -> Task:
        try:
            return active_kernel.unblock_task(task_id)
        except OrchestratorError as error:
            raise domain_error(error) from error

    @app.post("/api/v1/tasks/{task_id}/comments", response_model=Comment)
    def add_comment(task_id: str, payload: CommentCreate) -> Comment:
        try:
            return active_kernel.add_comment(task_id=task_id, body=payload.body, author=payload.author)
        except OrchestratorError as error:
            raise domain_error(error) from error

    @app.post("/api/v1/tasks/{task_id}/artifacts", response_model=Artifact)
    def add_artifact(task_id: str, payload: ArtifactCreate) -> Artifact:
        try:
            return active_kernel.add_artifact(
                task_id=task_id,
                filename=payload.filename,
                content_markdown=payload.content_markdown,
                description=payload.description,
            )
        except OrchestratorError as error:
            raise domain_error(error) from error

    @app.post("/api/v1/dependencies", status_code=204)
    def add_dependency(payload: DependencyCreate) -> None:
        try:
            active_kernel.add_dependency(payload.parent_id, payload.child_id)
        except OrchestratorError as error:
            raise domain_error(error) from error

    @app.get("/api/v1/events", response_model=list[Event])
    def list_events(since: int = 0) -> list[Event]:
        return active_kernel.list_events(since=since)

    @app.post("/runner/v1/lease", response_model=Lease | None)
    def lease_next(payload: LeaseRequest) -> Lease | None:
        return active_kernel.lease_next(runner_id=payload.runner_id, lease_seconds=payload.lease_seconds)

    @app.post("/runner/v1/runs/{run_id}/heartbeat", response_model=Run)
    def heartbeat(run_id: str, payload: HeartbeatRequest) -> Run:
        try:
            return active_kernel.heartbeat(run_id=run_id, lease_seconds=payload.lease_seconds)
        except OrchestratorError as error:
            raise domain_error(error) from error

    @app.post("/runner/v1/runs/{run_id}/finish", response_model=Run)
    def finish_run(run_id: str, payload: RunFinishRequest) -> Run:
        try:
            return active_kernel.complete_run(run_id=run_id, summary=payload.summary)
        except OrchestratorError as error:
            raise domain_error(error) from error

    @app.post("/runner/v1/runs/{run_id}/fail", response_model=Run)
    def fail_run(run_id: str, payload: RunFailRequest) -> Run:
        try:
            return active_kernel.fail_run(run_id=run_id, summary=payload.summary)
        except OrchestratorError as error:
            raise domain_error(error) from error

    @app.post("/runner/v1/runs/{run_id}/block", response_model=Run)
    def block_run(run_id: str, payload: RunBlockRequest) -> Run:
        try:
            return active_kernel.block_run(run_id=run_id, reason=payload.reason)
        except OrchestratorError as error:
            raise domain_error(error) from error

    return app


BOARD_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Kanban Agent Orchestrator</title>
  <style>
    :root { color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, sans-serif; background: #0f172a; color: #e2e8f0; }
    body { margin: 0; }
    header { padding: 18px 24px; border-bottom: 1px solid #334155; display: flex; align-items: center; justify-content: space-between; }
    h1 { font-size: 20px; margin: 0; }
    main { padding: 20px; }
    button, input, textarea, select { border-radius: 8px; border: 1px solid #475569; background: #111827; color: #e2e8f0; padding: 8px; }
    button { cursor: pointer; background: #2563eb; border-color: #2563eb; font-weight: 700; }
    .grid { display: grid; grid-template-columns: 320px 1fr; gap: 18px; align-items: start; }
    .panel { border: 1px solid #334155; border-radius: 12px; padding: 14px; background: #111827; }
    .form-row { display: grid; gap: 8px; margin-bottom: 10px; }
    .board { display: grid; grid-template-columns: repeat(5, minmax(180px, 1fr)); gap: 12px; overflow-x: auto; }
    .column { background: #020617; border: 1px solid #1e293b; border-radius: 12px; min-height: 280px; padding: 10px; }
    .column h2 { font-size: 14px; text-transform: uppercase; color: #93c5fd; margin: 0 0 10px; }
    .card { background: #1e293b; border: 1px solid #475569; border-radius: 10px; padding: 10px; margin-bottom: 10px; }
    .card h3 { margin: 0 0 6px; font-size: 15px; }
    .muted { color: #94a3b8; font-size: 12px; }
    .badge { display: inline-block; background: #334155; padding: 2px 6px; border-radius: 999px; margin-right: 4px; font-size: 11px; }
    pre { white-space: pre-wrap; max-height: 220px; overflow: auto; background: #020617; padding: 10px; border-radius: 8px; }
  </style>
</head>
<body>
<header>
  <h1>Kanban Agent Orchestrator</h1>
  <button onclick="refresh()">Refresh</button>
</header>
<main class="grid">
  <section class="panel">
    <h2>Create Task</h2>
    <div class="form-row"><label>Endpoint</label><select id="endpoint"></select></div>
    <div class="form-row"><label>Title</label><input id="title" placeholder="Implement the thing"></div>
    <div class="form-row"><label>Body</label><textarea id="body" rows="5"></textarea></div>
    <div class="form-row"><label>Priority</label><input id="priority" type="number" value="0"></div>
    <div class="form-row"><label><input id="exclusive" type="checkbox"> Exclusive</label></div>
    <button onclick="createTask()">Create Task</button>
    <hr>
    <h2>Create Endpoint</h2>
    <div class="form-row"><label>Name</label><input id="endpointName" placeholder="coder"></div>
    <div class="form-row"><label>Max concurrency</label><input id="endpointConcurrency" type="number" value="1" min="1"></div>
    <button onclick="createEndpoint()">Create Endpoint</button>
    <hr>
    <h2>Stats</h2>
    <pre id="stats">Loading...</pre>
  </section>
  <section class="board" id="board"></section>
</main>
<script>
const statuses = ["todo", "ready", "running", "blocked", "done"];
let snapshot = null;

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  if (!response.ok) throw new Error(await response.text());
  if (response.status === 204) return null;
  return response.json();
}

async function refresh() {
  snapshot = await api("/api/v1/snapshot");
  const stats = await api("/api/v1/stats");
  document.getElementById("stats").textContent = JSON.stringify(stats, null, 2);
  renderEndpoints();
  renderBoard();
}

function renderEndpoints() {
  const select = document.getElementById("endpoint");
  select.innerHTML = snapshot.agent_endpoints
    .map(endpoint => `<option value="${endpoint.id}">${endpoint.name} (${endpoint.max_concurrency})</option>`)
    .join("");
}

function renderBoard() {
  const tasksByStatus = Object.fromEntries(statuses.map(status => [status, []]));
  for (const task of snapshot.tasks) tasksByStatus[task.status]?.push(task);
  document.getElementById("board").innerHTML = statuses.map(status => `
    <div class="column">
      <h2>${status} (${tasksByStatus[status].length})</h2>
      ${tasksByStatus[status].map(renderCard).join("")}
    </div>
  `).join("");
}

function endpointName(id) {
  return snapshot.agent_endpoints.find(endpoint => endpoint.id === id)?.name || id;
}

function renderCard(task) {
  return `<article class="card">
    <h3>${escapeHtml(task.title)}</h3>
    <div class="muted">${endpointName(task.agent_endpoint_id)}</div>
    <p>${escapeHtml(task.body || "")}</p>
    <span class="badge">priority ${task.priority}</span>
    ${task.exclusive ? '<span class="badge">exclusive</span>' : ''}
    <span class="badge">parents ${task.parent_ids.length}</span>
    <span class="badge">children ${task.child_ids.length}</span>
  </article>`;
}

async function createEndpoint() {
  await api("/api/v1/agent-endpoints", { method: "POST", body: JSON.stringify({
    name: document.getElementById("endpointName").value || "agent",
    max_concurrency: Number(document.getElementById("endpointConcurrency").value || 1),
  }) });
  await refresh();
}

async function createTask() {
  await api("/api/v1/tasks", { method: "POST", body: JSON.stringify({
    agent_endpoint_id: document.getElementById("endpoint").value,
    title: document.getElementById("title").value,
    body: document.getElementById("body").value,
    priority: Number(document.getElementById("priority").value || 0),
    exclusive: document.getElementById("exclusive").checked,
  }) });
  document.getElementById("title").value = "";
  document.getElementById("body").value = "";
  await refresh();
}

function escapeHtml(value) {
  return value.replace(/[&<>'"]/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
}

refresh().catch(error => { document.body.innerHTML = `<pre>${escapeHtml(String(error))}</pre>`; });
</script>
</body>
</html>
"""


app = create_app()
