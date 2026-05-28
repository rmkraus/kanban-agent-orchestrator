from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from kanban_agent_orchestrator.errors import NotFoundError, OrchestratorError
from kanban_agent_orchestrator.kernel import OrchestratorKernel
from kanban_agent_orchestrator.models import (
    AgentEndpoint,
    Artifact,
    Comment,
    Event,
    Lease,
    Question,
    Run,
    RunnerCreateResult,
    RunnerPublic,
    Task,
    TaskDetail,
    TaskStatus,
)

STATIC_DIR = Path(__file__).parent / "static"


class RunnerCreate(BaseModel):
    name: str
    enabled: bool = True


class RunnerUpdate(BaseModel):
    name: str | None = None
    enabled: bool | None = None


class AgentEndpointCreate(BaseModel):
    name: str
    max_concurrency: int = 1
    enabled: bool = True
    runner_id: str | None = None


class AgentEndpointUpdate(BaseModel):
    name: str | None = None
    max_concurrency: int | None = None
    enabled: bool | None = None
    runner_id: str | None = None


class TaskCreate(BaseModel):
    title: str
    agent_endpoint_id: str
    body: str = ""
    priority: int = 0
    exclusive: bool = False
    parent_ids: list[str] = Field(default_factory=list)
    created_by: str = "system"


class TaskUpdate(BaseModel):
    title: str | None = None
    agent_endpoint_id: str | None = None
    body: str | None = None
    priority: int | None = None
    exclusive: bool | None = None


class AgentTaskCreate(BaseModel):
    title: str
    assignee: str
    body: str = ""
    priority: int = 0
    exclusive: bool = False
    parent_id: str | None = None
    dependency_ids: list[str] = Field(default_factory=list)
    created_by: str = "agent"


class RunTaskCreate(BaseModel):
    title: str
    assignee: str
    body: str = ""
    priority: int = 0
    exclusive: bool = False
    parent_current_task: bool = True
    dependency_ids: list[str] = Field(default_factory=list)


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


class RunQuestionCreate(BaseModel):
    body: str
    resolves_block: bool = True
    block_reason: str | None = None


class QuestionAnswerCreate(BaseModel):
    body: str
    answered_by: str = "human"
    unblock_if_resolved: bool = True


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


class ApiSnapshot(BaseModel):
    runners: list[RunnerPublic]
    agent_endpoints: list[AgentEndpoint]
    tasks: list[Task]
    runs: list[Run]
    events: list[Event]
    comments: list[Comment]
    questions: list[Question]
    artifacts: list[Artifact]
    next_event_id: int


def domain_error(error: OrchestratorError) -> HTTPException:
    status_code = 404 if isinstance(error, NotFoundError) else 400
    return HTTPException(status_code=status_code, detail=str(error))


def bearer_psk(authorization: str | None) -> str | None:
    return authorization.removeprefix("Bearer ").strip() if authorization else None


def create_public_app(kernel: OrchestratorKernel | None = None) -> FastAPI:
    active_kernel = kernel or OrchestratorKernel.persistent()
    app = FastAPI(title="Kanban Agent Orchestrator", version="0.1.0")

    assets_dir = STATIC_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/", response_class=HTMLResponse, response_model=None)
    def board():
        index_path = STATIC_DIR / "index.html"
        if index_path.exists():
            return FileResponse(index_path)
        return "<h1>Kanban Agent Orchestrator</h1><p>Frontend build not found. Run <code>npm run build --prefix web</code>.</p>"

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "surface": "public"}

    @app.get("/api/v1/snapshot", response_model=ApiSnapshot)
    def snapshot() -> ApiSnapshot:
        active_kernel.reclaim_expired_leases()
        active_kernel.recompute_readiness()
        current = active_kernel.snapshot()
        return ApiSnapshot(
            runners=active_kernel.list_runners(),
            agent_endpoints=current.agent_endpoints,
            tasks=current.tasks,
            runs=current.runs,
            events=current.events,
            comments=current.comments,
            questions=current.questions,
            artifacts=current.artifacts,
            next_event_id=current.next_event_id,
        )

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

    @app.get("/api/v1/runners", response_model=list[RunnerPublic])
    def list_runners() -> list[RunnerPublic]:
        return active_kernel.list_runners()

    @app.post("/api/v1/runners", response_model=RunnerCreateResult)
    def create_runner(payload: RunnerCreate) -> RunnerCreateResult:
        return active_kernel.create_runner(name=payload.name, enabled=payload.enabled)

    @app.patch("/api/v1/runners/{runner_id}", response_model=RunnerPublic)
    def update_runner(runner_id: str, payload: RunnerUpdate) -> RunnerPublic:
        try:
            return active_kernel.update_runner(runner_id=runner_id, name=payload.name, enabled=payload.enabled)
        except OrchestratorError as error:
            raise domain_error(error) from error

    @app.delete("/api/v1/runners/{runner_id}", status_code=204)
    def delete_runner(runner_id: str) -> None:
        try:
            active_kernel.delete_runner(runner_id=runner_id)
        except OrchestratorError as error:
            raise domain_error(error) from error

    @app.get("/api/v1/agent-endpoints", response_model=list[AgentEndpoint])
    def list_agent_endpoints() -> list[AgentEndpoint]:
        return active_kernel.list_agent_endpoints()

    @app.post("/api/v1/agent-endpoints", response_model=AgentEndpoint)
    def create_agent_endpoint(payload: AgentEndpointCreate) -> AgentEndpoint:
        try:
            return active_kernel.create_agent_endpoint(
                name=payload.name, max_concurrency=payload.max_concurrency, enabled=payload.enabled, runner_id=payload.runner_id
            )
        except OrchestratorError as error:
            raise domain_error(error) from error

    @app.patch("/api/v1/agent-endpoints/{endpoint_id}", response_model=AgentEndpoint)
    def update_agent_endpoint(endpoint_id: str, payload: AgentEndpointUpdate) -> AgentEndpoint:
        try:
            return active_kernel.update_agent_endpoint(
                endpoint_id=endpoint_id,
                name=payload.name,
                max_concurrency=payload.max_concurrency,
                enabled=payload.enabled,
                runner_id=payload.runner_id,
            )
        except OrchestratorError as error:
            raise domain_error(error) from error

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
                parent_ids=payload.parent_ids,
                created_by=payload.created_by,
            )
        except OrchestratorError as error:
            raise domain_error(error) from error

    @app.patch("/api/v1/tasks/{task_id}", response_model=Task)
    def update_task(task_id: str, payload: TaskUpdate) -> Task:
        try:
            return active_kernel.update_task(
                task_id=task_id,
                title=payload.title,
                agent_endpoint_id=payload.agent_endpoint_id,
                body=payload.body,
                priority=payload.priority,
                exclusive=payload.exclusive,
            )
        except OrchestratorError as error:
            raise domain_error(error) from error

    @app.post("/api/v1/agent-tasks", response_model=Task)
    def create_agent_task(payload: AgentTaskCreate) -> Task:
        try:
            return active_kernel.create_task_for_agent(
                title=payload.title,
                assignee=payload.assignee,
                body=payload.body,
                priority=payload.priority,
                exclusive=payload.exclusive,
                parent_id=payload.parent_id,
                dependency_ids=payload.dependency_ids,
                created_by=payload.created_by,
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

    @app.post("/api/v1/tasks/{task_id}/questions/{question_id}/answer", response_model=Question)
    def answer_question(task_id: str, question_id: str, payload: QuestionAnswerCreate) -> Question:
        try:
            return active_kernel.answer_question(
                task_id=task_id,
                question_id=question_id,
                body=payload.body,
                answered_by=payload.answered_by,
                unblock_if_resolved=payload.unblock_if_resolved,
            )
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

    return app


def register_runner_routes(app: FastAPI, active_kernel: OrchestratorKernel) -> None:
    @app.post("/runner/v1/lease", response_model=Lease | None)
    def lease_next(payload: LeaseRequest, authorization: str | None = Header(default=None)) -> Lease | None:
        try:
            return active_kernel.lease_next(runner_id=payload.runner_id, lease_seconds=payload.lease_seconds, psk=bearer_psk(authorization))
        except OrchestratorError as error:
            raise domain_error(error) from error

    @app.post("/runner/v1/runs/{run_id}/heartbeat", response_model=Run)
    def heartbeat(run_id: str, payload: HeartbeatRequest, authorization: str | None = Header(default=None)) -> Run:
        try:
            active_kernel.authenticate_run(run_id, bearer_psk(authorization))
            return active_kernel.heartbeat(run_id=run_id, lease_seconds=payload.lease_seconds)
        except OrchestratorError as error:
            raise domain_error(error) from error

    @app.post("/runner/v1/runs/{run_id}/finish", response_model=Run)
    def finish_run(run_id: str, payload: RunFinishRequest, authorization: str | None = Header(default=None)) -> Run:
        try:
            active_kernel.authenticate_run(run_id, bearer_psk(authorization))
            return active_kernel.complete_run(run_id=run_id, summary=payload.summary)
        except OrchestratorError as error:
            raise domain_error(error) from error

    @app.post("/runner/v1/runs/{run_id}/fail", response_model=Run)
    def fail_run(run_id: str, payload: RunFailRequest, authorization: str | None = Header(default=None)) -> Run:
        try:
            active_kernel.authenticate_run(run_id, bearer_psk(authorization))
            return active_kernel.fail_run(run_id=run_id, summary=payload.summary)
        except OrchestratorError as error:
            raise domain_error(error) from error

    @app.post("/runner/v1/runs/{run_id}/block", response_model=Run)
    def block_run(run_id: str, payload: RunBlockRequest, authorization: str | None = Header(default=None)) -> Run:
        try:
            active_kernel.authenticate_run(run_id, bearer_psk(authorization))
            return active_kernel.block_run(run_id=run_id, reason=payload.reason)
        except OrchestratorError as error:
            raise domain_error(error) from error

    @app.post("/runner/v1/runs/{run_id}/questions", response_model=Question)
    def ask_question_from_run(run_id: str, payload: RunQuestionCreate, authorization: str | None = Header(default=None)) -> Question:
        try:
            active_kernel.authenticate_run(run_id, bearer_psk(authorization))
            return active_kernel.ask_question_from_run(
                run_id=run_id,
                body=payload.body,
                resolves_block=payload.resolves_block,
                block_reason=payload.block_reason,
            )
        except OrchestratorError as error:
            raise domain_error(error) from error

    @app.post("/runner/v1/runs/{run_id}/tasks", response_model=Task)
    def create_task_from_run(run_id: str, payload: RunTaskCreate, authorization: str | None = Header(default=None)) -> Task:
        try:
            active_kernel.authenticate_run(run_id, bearer_psk(authorization))
            return active_kernel.create_task_from_run(
                run_id=run_id,
                title=payload.title,
                assignee=payload.assignee,
                body=payload.body,
                priority=payload.priority,
                exclusive=payload.exclusive,
                parent_current_task=payload.parent_current_task,
                dependency_ids=payload.dependency_ids,
            )
        except OrchestratorError as error:
            raise domain_error(error) from error

    return


def create_runner_app(kernel: OrchestratorKernel | None = None) -> FastAPI:
    active_kernel = kernel or OrchestratorKernel.persistent()
    app = FastAPI(title="Kanban Agent Orchestrator Runner API", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "surface": "runner"}

    register_runner_routes(app, active_kernel)
    return app


# Backward-compatible ASGI app for tests/imports. The CLI serves public and runner apps on separate ports.
def create_app(kernel: OrchestratorKernel | None = None, include_runner_api: bool = True) -> FastAPI:
    active_kernel = kernel or OrchestratorKernel.persistent()
    app = create_public_app(active_kernel)
    if include_runner_api:
        register_runner_routes(app, active_kernel)
    return app


app = create_public_app()
runner_app = create_runner_app()
