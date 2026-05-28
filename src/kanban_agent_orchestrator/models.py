from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class TaskStatus(StrEnum):
    SCOPING = "scoping"
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


class QuestionStatus(StrEnum):
    OPEN = "open"
    ANSWERED = "answered"


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
    DELETED = "deleted"
    QUESTION_ASKED = "question_asked"
    QUESTION_ANSWERED = "question_answered"
    UPDATED = "updated"


class Runner(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    psk_hash: str
    enabled: bool = True
    created_at: datetime = Field(default_factory=utc_now)
    last_seen_at: datetime | None = None


class RunnerPublic(BaseModel):
    id: str
    name: str
    enabled: bool = True
    created_at: datetime
    last_seen_at: datetime | None = None


class RunnerCreateResult(BaseModel):
    runner: RunnerPublic
    psk: str


class AgentEndpoint(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    max_concurrency: int = Field(ge=1, default=1)
    enabled: bool = True
    created_at: datetime = Field(default_factory=utc_now)
    runner_id: str | None = None


class Task(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    context_id: str = Field(default_factory=lambda: str(uuid4()))
    title: str
    body: str = ""
    agent_endpoint_id: str
    status: TaskStatus = TaskStatus.SCOPING
    priority: int = 0
    exclusive: bool = False
    parent_ids: set[str] = Field(default_factory=set)
    child_ids: set[str] = Field(default_factory=set)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None


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


class Question(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str
    run_id: str | None = None
    asked_by: str = "agent"
    body: str
    status: QuestionStatus = QuestionStatus.OPEN
    resolves_block: bool = True
    answer_body: str | None = None
    answered_by: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    answered_at: datetime | None = None


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
    history: list[Comment]
    questions: list[Question]
    artifacts: list[Artifact]
    events: list[Event]


class Snapshot(BaseModel):
    runners: list[Runner] = Field(default_factory=list)
    agent_endpoints: list[AgentEndpoint] = Field(default_factory=list)
    tasks: list[Task] = Field(default_factory=list)
    runs: list[Run] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    comments: list[Comment] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    next_event_id: int = 1
