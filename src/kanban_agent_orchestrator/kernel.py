from datetime import UTC, datetime, timedelta
from pathlib import Path

from kanban_agent_orchestrator.errors import DependencyCycleError, InvalidTransitionError, NotFoundError
from kanban_agent_orchestrator.models import (
    AgentEndpoint,
    Artifact,
    Comment,
    Event,
    EventKind,
    Lease,
    Run,
    RunStatus,
    Snapshot,
    Task,
    TaskDetail,
    TaskStatus,
    utc_now,
)
from kanban_agent_orchestrator.store import JsonStore


class OrchestratorKernel:
    def __init__(self, store: JsonStore | None = None, persist: bool = False) -> None:
        self.store = store
        self.persist = persist
        snapshot = store.load() if store is not None else Snapshot()
        self.agent_endpoints: dict[str, AgentEndpoint] = {endpoint.id: endpoint for endpoint in snapshot.agent_endpoints}
        self.tasks: dict[str, Task] = {task.id: task for task in snapshot.tasks}
        self.runs: dict[str, Run] = {run.id: run for run in snapshot.runs}
        self.events: list[Event] = snapshot.events
        self.comments: dict[str, Comment] = {comment.id: comment for comment in snapshot.comments}
        self.artifacts: dict[str, Artifact] = {artifact.id: artifact for artifact in snapshot.artifacts}
        self.next_event_id = snapshot.next_event_id

    @classmethod
    def persistent(cls, path: str | Path | None = None) -> "OrchestratorKernel":
        return cls(store=JsonStore(path), persist=True)

    def snapshot(self) -> Snapshot:
        return Snapshot(
            agent_endpoints=list(self.agent_endpoints.values()),
            tasks=list(self.tasks.values()),
            runs=list(self.runs.values()),
            events=self.events,
            comments=list(self.comments.values()),
            artifacts=list(self.artifacts.values()),
            next_event_id=self.next_event_id,
        )

    def create_agent_endpoint(self, name: str, max_concurrency: int = 1, enabled: bool = True) -> AgentEndpoint:
        endpoint = AgentEndpoint(name=name, max_concurrency=max_concurrency, enabled=enabled)
        self.agent_endpoints[endpoint.id] = endpoint
        self._event(EventKind.CREATED, f"Agent endpoint created: {name}", metadata={"agent_endpoint_id": endpoint.id})
        self._save()
        return endpoint

    def list_agent_endpoints(self) -> list[AgentEndpoint]:
        return sorted(self.agent_endpoints.values(), key=lambda endpoint: endpoint.created_at)

    def create_task(
        self,
        title: str,
        agent_endpoint_id: str,
        body: str = "",
        priority: int = 0,
        exclusive: bool = False,
        status: TaskStatus = TaskStatus.TODO,
    ) -> Task:
        if agent_endpoint_id not in self.agent_endpoints:
            raise NotFoundError(f"agent endpoint not found: {agent_endpoint_id}")

        task = Task(title=title, body=body, agent_endpoint_id=agent_endpoint_id, priority=priority, exclusive=exclusive, status=status)
        self.tasks[task.id] = task
        self._event(EventKind.CREATED, f"Task created: {title}", task_id=task.id)
        self.recompute_readiness()
        self._save()
        return task

    def list_tasks(self, status: TaskStatus | None = None) -> list[Task]:
        tasks = self.tasks.values()
        if status is not None:
            tasks = [task for task in tasks if task.status == status]
        return sorted(tasks, key=lambda task: (-task.priority, task.created_at))

    def task_detail(self, task_id: str) -> TaskDetail:
        task = self._task(task_id)
        return TaskDetail(
            task=task,
            parents=[self._task(parent_id) for parent_id in sorted(task.parent_ids)],
            children=[self._task(child_id) for child_id in sorted(task.child_ids)],
            runs=[run for run in self.runs.values() if run.task_id == task_id],
            comments=[comment for comment in self.comments.values() if comment.task_id == task_id],
            artifacts=[artifact for artifact in self.artifacts.values() if artifact.task_id == task_id],
            events=[event for event in self.events if event.task_id == task_id],
        )

    def add_dependency(self, parent_id: str, child_id: str) -> None:
        parent = self._task(parent_id)
        child = self._task(child_id)
        if parent_id == child_id or self._has_path(child_id, parent_id):
            raise DependencyCycleError(f"dependency would create cycle: {parent_id} -> {child_id}")

        parent.child_ids.add(child.id)
        child.parent_ids.add(parent.id)
        parent.updated_at = utc_now()
        child.updated_at = parent.updated_at
        self._event(EventKind.DEPENDENCY, f"Dependency added: {parent.title} -> {child.title}", task_id=child.id, metadata={"parent_id": parent.id})
        self.recompute_readiness()
        self._save()

    def recompute_readiness(self) -> None:
        for task in self.tasks.values():
            if task.status not in {TaskStatus.TODO, TaskStatus.READY}:
                continue
            next_status = TaskStatus.READY if self._parents_done(task) else TaskStatus.TODO
            if task.status != next_status:
                task.status = next_status
                task.updated_at = utc_now()
                if next_status == TaskStatus.READY:
                    self._event(EventKind.READY, "Task became ready", task_id=task.id)

    def lease_next(self, runner_id: str, lease_seconds: int = 300, now: datetime | None = None) -> Lease | None:
        now = now or datetime.now(UTC)
        self.reclaim_expired_leases(now)
        self.recompute_readiness()

        active_runs = self._active_runs(now)
        if self._exclusive_task_running() or (active_runs and self._ready_exclusive_tasks()):
            return None

        for task in self._ready_tasks_by_priority():
            endpoint = self.agent_endpoints.get(task.agent_endpoint_id)
            if endpoint is None or not endpoint.enabled:
                continue
            if self._active_run_count(endpoint.id, now) >= endpoint.max_concurrency:
                continue
            if task.exclusive and self._active_runs(now):
                continue

            task.status = TaskStatus.RUNNING
            task.updated_at = now
            run = Run(
                task_id=task.id,
                runner_id=runner_id,
                agent_endpoint_id=endpoint.id,
                leased_until=now + timedelta(seconds=lease_seconds),
                started_at=now,
                heartbeat_at=now,
            )
            self.runs[run.id] = run
            self._event(EventKind.CLAIMED, f"Task leased by {runner_id}", task_id=task.id, run_id=run.id, metadata={"runner_id": runner_id})
            self._save()
            return Lease(run=run, task=task)

        self._save()
        return None

    def heartbeat(self, run_id: str, lease_seconds: int = 300, now: datetime | None = None) -> Run:
        now = now or datetime.now(UTC)
        run = self._run(run_id)
        if run.status not in {RunStatus.LEASED, RunStatus.RUNNING}:
            raise InvalidTransitionError(f"cannot heartbeat terminal run: {run_id}")
        run.status = RunStatus.RUNNING
        run.heartbeat_at = now
        run.leased_until = now + timedelta(seconds=lease_seconds)
        self._event(EventKind.HEARTBEAT, "Run heartbeat", task_id=run.task_id, run_id=run.id, metadata={"runner_id": run.runner_id})
        self._save()
        return run

    def complete_run(self, run_id: str, summary: str = "") -> Run:
        run = self._run(run_id)
        if run.status not in {RunStatus.LEASED, RunStatus.RUNNING}:
            raise InvalidTransitionError(f"cannot complete terminal run: {run_id}")
        task = self._task(run.task_id)
        now = utc_now()
        run.status = RunStatus.COMPLETED
        run.summary = summary
        run.finished_at = now
        task.status = TaskStatus.DONE
        task.updated_at = now
        self._event(EventKind.COMPLETED, "Run completed", task_id=task.id, run_id=run.id, metadata={"summary": summary})
        self.recompute_readiness()
        self._save()
        return run

    def fail_run(self, run_id: str, summary: str = "") -> Run:
        run = self._run(run_id)
        if run.status not in {RunStatus.LEASED, RunStatus.RUNNING}:
            raise InvalidTransitionError(f"cannot fail terminal run: {run_id}")
        task = self._task(run.task_id)
        now = utc_now()
        run.status = RunStatus.FAILED
        run.summary = summary
        run.finished_at = now
        task.status = TaskStatus.READY if self._parents_done(task) else TaskStatus.TODO
        task.updated_at = now
        self._event(EventKind.FAILED, "Run failed", task_id=task.id, run_id=run.id, metadata={"summary": summary})
        self._save()
        return run

    def block_run(self, run_id: str, reason: str) -> Run:
        run = self._run(run_id)
        if run.status not in {RunStatus.LEASED, RunStatus.RUNNING}:
            raise InvalidTransitionError(f"cannot block terminal run: {run_id}")
        task = self._task(run.task_id)
        now = utc_now()
        run.status = RunStatus.BLOCKED
        run.summary = reason
        run.finished_at = now
        task.status = TaskStatus.BLOCKED
        task.updated_at = now
        self._event(EventKind.BLOCKED, "Run blocked", task_id=task.id, run_id=run.id, metadata={"reason": reason})
        self._save()
        return run

    def unblock_task(self, task_id: str) -> Task:
        task = self._task(task_id)
        if task.status != TaskStatus.BLOCKED:
            raise InvalidTransitionError(f"cannot unblock non-blocked task: {task_id}")
        task.status = TaskStatus.READY if self._parents_done(task) else TaskStatus.TODO
        task.updated_at = utc_now()
        self._event(EventKind.READY, "Task unblocked", task_id=task.id)
        self._save()
        return task

    def add_comment(self, task_id: str, body: str, author: str = "system") -> Comment:
        self._task(task_id)
        comment = Comment(task_id=task_id, body=body, author=author)
        self.comments[comment.id] = comment
        self._event(EventKind.COMMENTED, f"Comment by {author}", task_id=task_id)
        self._save()
        return comment

    def add_artifact(self, task_id: str, filename: str, content_markdown: str, description: str = "") -> Artifact:
        self._task(task_id)
        artifact = Artifact(task_id=task_id, filename=filename, content_markdown=content_markdown, description=description)
        self.artifacts[artifact.id] = artifact
        self._event(EventKind.ARTIFACT, f"Artifact saved: {filename}", task_id=task_id, metadata={"artifact_id": artifact.id})
        self._save()
        return artifact

    def list_events(self, since: int = 0) -> list[Event]:
        return [event for event in self.events if event.id > since]

    def reclaim_expired_leases(self, now: datetime | None = None) -> list[Run]:
        now = now or datetime.now(UTC)
        reclaimed = []
        for run in self.runs.values():
            if run.status in {RunStatus.LEASED, RunStatus.RUNNING} and run.leased_until <= now:
                run.status = RunStatus.FAILED
                run.finished_at = now
                task = self._task(run.task_id)
                task.status = TaskStatus.READY if self._parents_done(task) else TaskStatus.TODO
                task.updated_at = now
                reclaimed.append(run)
                self._event(EventKind.RECLAIMED, "Expired lease reclaimed", task_id=task.id, run_id=run.id)
        if reclaimed:
            self._save()
        return reclaimed

    def _task(self, task_id: str) -> Task:
        try:
            return self.tasks[task_id]
        except KeyError as error:
            raise NotFoundError(f"task not found: {task_id}") from error

    def _run(self, run_id: str) -> Run:
        try:
            return self.runs[run_id]
        except KeyError as error:
            raise NotFoundError(f"run not found: {run_id}") from error

    def _parents_done(self, task: Task) -> bool:
        return all(self.tasks[parent_id].status == TaskStatus.DONE for parent_id in task.parent_ids)

    def _has_path(self, start_id: str, target_id: str) -> bool:
        stack = [start_id]
        seen: set[str] = set()
        while stack:
            current_id = stack.pop()
            if current_id == target_id:
                return True
            if current_id in seen:
                continue
            seen.add(current_id)
            stack.extend(self.tasks[current_id].child_ids)
        return False

    def _ready_tasks_by_priority(self) -> list[Task]:
        return sorted((task for task in self.tasks.values() if task.status == TaskStatus.READY), key=lambda task: (-task.priority, task.created_at))

    def _ready_exclusive_tasks(self) -> list[Task]:
        return [task for task in self.tasks.values() if task.status == TaskStatus.READY and task.exclusive]

    def _active_runs(self, now: datetime) -> list[Run]:
        return [run for run in self.runs.values() if run.status in {RunStatus.LEASED, RunStatus.RUNNING} and run.leased_until > now]

    def _active_run_count(self, agent_endpoint_id: str, now: datetime) -> int:
        return sum(1 for run in self._active_runs(now) if run.agent_endpoint_id == agent_endpoint_id)

    def _exclusive_task_running(self) -> bool:
        active_task_ids = {run.task_id for run in self.runs.values() if run.status in {RunStatus.LEASED, RunStatus.RUNNING}}
        return any(self.tasks[task_id].exclusive for task_id in active_task_ids)

    def _event(
        self,
        kind: EventKind,
        message: str,
        task_id: str | None = None,
        run_id: str | None = None,
        metadata: dict[str, str | int | bool | None] | None = None,
    ) -> Event:
        event = Event(id=self.next_event_id, kind=kind, message=message, task_id=task_id, run_id=run_id, metadata=metadata or {})
        self.next_event_id += 1
        self.events.append(event)
        return event

    def _save(self) -> None:
        if self.store is not None and self.persist:
            self.store.save(self.snapshot())
