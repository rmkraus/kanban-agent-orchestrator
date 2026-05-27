from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class TaskStatus(StrEnum):
    TODO = "todo"
    READY = "ready"
    RUNNING = "running"
    BLOCKED = "blocked"
    DONE = "done"
    ARCHIVED = "archived"


class RunStatus(StrEnum):
    LEASED = "leased"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


class EventKind(StrEnum):
    CREATED = "created"
    READY = "ready"
    CLAIMED = "claimed"
    HEARTBEAT = "heartbeat"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    COMMENTED = "commented"
    ARTIFACT = "artifact"
    DEPENDENCY = "dependency"
    RECLAIMED = "reclaimed"


class AgentEndpoint(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    max_concurrency: int = Field(ge=1, default=1)
    enabled: bool = True
    created_at: datetime = Field(default_factory=utc_now)


class Task(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    title: str
    body: str = ""
    agent_endpoint_id: str
    status: TaskStatus = TaskStatus.TODO
    priority: int = 0
    exclusive: bool = False
    parent_ids: set[str] = Field(default_factory=set)
    child_ids: set[str] = Field(default_factory=set)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Run(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str
    runner_id: str
    agent_endpoint_id: str
    status: RunStatus = RunStatus.LEASED
    leased_until: datetime = Field(default_factory=lambda: utc_now() + timedelta(minutes=5))
    started_at: datetime = Field(default_factory=utc_now)
    heartbeat_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None
    summary: str | None = None


class Event(BaseModel):
    id: int
    kind: EventKind
    message: str
    task_id: str | None = None
    run_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, str | int | bool | None] = Field(default_factory=dict)


class Comment(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str
    author: str = "system"
    body: str
    created_at: datetime = Field(default_factory=utc_now)


class Artifact(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str
    filename: str
    content_markdown: str
    description: str = ""
    created_at: datetime = Field(default_factory=utc_now)


class Lease(BaseModel):
    run: Run
    task: Task


class TaskDetail(BaseModel):
    task: Task
    parents: list[Task]
    children: list[Task]
    runs: list[Run]
    comments: list[Comment]
    artifacts: list[Artifact]
    events: list[Event]


class Snapshot(BaseModel):
    agent_endpoints: list[AgentEndpoint] = Field(default_factory=list)
    tasks: list[Task] = Field(default_factory=list)
    runs: list[Run] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    comments: list[Comment] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    next_event_id: int = 1
