import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from kanban_agent_orchestrator.errors import DependencyCycleError, InvalidTransitionError, NotFoundError
from kanban_agent_orchestrator.models import (
    AgentEndpoint,
    Artifact,
    Comment,
    Event,
    EventKind,
    Lease,
    Question,
    QuestionStatus,
    Run,
    Runner,
    RunnerCreateResult,
    RunnerPublic,
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
        self.runners: dict[str, Runner] = {runner.id: runner for runner in snapshot.runners}
        self.agent_endpoints: dict[str, AgentEndpoint] = {endpoint.id: endpoint for endpoint in snapshot.agent_endpoints}
        self.tasks: dict[str, Task] = {task.id: task for task in snapshot.tasks}
        self.runs: dict[str, Run] = {run.id: run for run in snapshot.runs}
        self.events: list[Event] = snapshot.events
        self.comments: dict[str, Comment] = {comment.id: comment for comment in snapshot.comments}
        self.questions: dict[str, Question] = {question.id: question for question in snapshot.questions}
        self.artifacts: dict[str, Artifact] = {artifact.id: artifact for artifact in snapshot.artifacts}
        self.next_event_id = snapshot.next_event_id

    @classmethod
    def persistent(cls, path: str | Path | None = None) -> "OrchestratorKernel":
        return cls(store=JsonStore(path), persist=True)

    def snapshot(self) -> Snapshot:
        return Snapshot(
            runners=list(self.runners.values()),
            agent_endpoints=list(self.agent_endpoints.values()),
            tasks=list(self.tasks.values()),
            runs=list(self.runs.values()),
            events=self.events,
            comments=list(self.comments.values()),
            questions=list(self.questions.values()),
            artifacts=list(self.artifacts.values()),
            next_event_id=self.next_event_id,
        )

    def create_runner(self, name: str, enabled: bool = True) -> RunnerCreateResult:
        psk = f"kanban_rnr_{secrets.token_urlsafe(32)}"
        runner = Runner(name=name, psk_hash=self._hash_psk(psk), enabled=enabled)
        self.runners[runner.id] = runner
        self._event(EventKind.CREATED, f"Runner created: {name}", metadata={"runner_id": runner.id})
        self._save()
        return RunnerCreateResult(runner=self._public_runner(runner), psk=psk)

    def list_runners(self) -> list[RunnerPublic]:
        return [self._public_runner(runner) for runner in sorted(self.runners.values(), key=lambda runner: runner.created_at)]

    def update_runner(self, runner_id: str, name: str | None = None, enabled: bool | None = None) -> RunnerPublic:
        runner = self._runner(runner_id)
        if name is not None:
            runner.name = name
        if enabled is not None:
            runner.enabled = enabled
        self._save()
        return self._public_runner(runner)

    def delete_runner(self, runner_id: str, now: datetime | None = None) -> None:
        now = now or utc_now()
        runner = self._runner(runner_id)
        active_runs = [
            run for run in self.runs.values() if run.runner_id == runner_id and run.status in {RunStatus.LEASED, RunStatus.RUNNING} and run.leased_until > now
        ]
        if active_runs:
            raise InvalidTransitionError(f"cannot delete runner with active runs: {runner_id}")

        disabled_backend_ids = []
        for endpoint in self.agent_endpoints.values():
            if endpoint.runner_id == runner_id:
                endpoint.enabled = False
                endpoint.runner_id = None
                disabled_backend_ids.append(endpoint.id)

        del self.runners[runner.id]
        self._event(
            EventKind.DELETED, f"Runner deleted: {runner.name}", metadata={"runner_id": runner.id, "disabled_backend_ids": ",".join(disabled_backend_ids)}
        )
        self._save()

    def create_agent_endpoint(self, name: str, max_concurrency: int = 1, enabled: bool = True, runner_id: str | None = None) -> AgentEndpoint:
        if runner_id is not None:
            self._runner(runner_id)
        endpoint = AgentEndpoint(name=name, max_concurrency=max_concurrency, enabled=enabled, runner_id=runner_id)
        self.agent_endpoints[endpoint.id] = endpoint
        self._event(EventKind.CREATED, f"Backend created: {name}", metadata={"agent_endpoint_id": endpoint.id, "runner_id": runner_id})
        self._save()
        return endpoint

    def update_agent_endpoint(
        self, endpoint_id: str, name: str | None = None, max_concurrency: int | None = None, enabled: bool | None = None, runner_id: str | None = None
    ) -> AgentEndpoint:
        endpoint = self._agent_endpoint(endpoint_id)
        if runner_id is not None:
            self._runner(runner_id)
            endpoint.runner_id = runner_id
        if name is not None:
            endpoint.name = name
        if max_concurrency is not None:
            endpoint.max_concurrency = max_concurrency
        if enabled is not None:
            endpoint.enabled = enabled
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
        status: TaskStatus = TaskStatus.SCOPING,
        parent_ids: list[str] | None = None,
        created_by: str = "system",
    ) -> Task:
        self._agent_endpoint(agent_endpoint_id)
        parents = []
        for parent_id in parent_ids or []:
            parent = self._task(parent_id)
            if parent.id not in parents:
                parents.append(parent.id)

        task = Task(title=title, body=body, agent_endpoint_id=agent_endpoint_id, priority=priority, exclusive=exclusive, status=status)
        task.parent_ids.update(parents)
        self.tasks[task.id] = task
        for parent_id in parents:
            parent = self._task(parent_id)
            parent.child_ids.add(task.id)
            parent.updated_at = utc_now()
        self._event(EventKind.CREATED, f"Task created: {title}", task_id=task.id, metadata={"created_by": created_by, "agent_endpoint_id": agent_endpoint_id})
        for parent_id in parents:
            self._event(
                EventKind.DEPENDENCY, f"Dependency added: {self._task(parent_id).title} -> {task.title}", task_id=task.id, metadata={"parent_id": parent_id}
            )
        self.recompute_readiness()
        self._save()
        return task

    def create_task_for_agent(
        self,
        title: str,
        assignee: str,
        body: str = "",
        priority: int = 0,
        exclusive: bool = False,
        parent_id: str | None = None,
        dependency_ids: list[str] | None = None,
        created_by: str = "agent",
    ) -> Task:
        endpoint = self.agent_endpoint_by_name(assignee)
        parent_ids = [parent_id] if parent_id is not None else []
        parent_ids.extend(dependency_ids or [])
        return self.create_task(
            title=title,
            agent_endpoint_id=endpoint.id,
            body=body,
            priority=priority,
            exclusive=exclusive,
            parent_ids=parent_ids,
            created_by=created_by,
        )

    def create_task_from_run(
        self,
        run_id: str,
        title: str,
        assignee: str,
        body: str = "",
        priority: int = 0,
        exclusive: bool = False,
        parent_current_task: bool = True,
        dependency_ids: list[str] | None = None,
    ) -> Task:
        run = self._run(run_id)
        if run.status not in {RunStatus.LEASED, RunStatus.RUNNING}:
            raise InvalidTransitionError(f"cannot create task from terminal run: {run_id}")
        parent_id = run.task_id if parent_current_task else None
        return self.create_task_for_agent(
            title=title,
            assignee=assignee,
            body=body,
            priority=priority,
            exclusive=exclusive,
            parent_id=parent_id,
            dependency_ids=dependency_ids,
            created_by=run.runner_id,
        )

    def agent_endpoint_by_name(self, name: str) -> AgentEndpoint:
        matches = [endpoint for endpoint in self.agent_endpoints.values() if endpoint.name == name]
        if not matches:
            raise NotFoundError(f"agent endpoint not found by name: {name}")
        if len(matches) > 1:
            raise InvalidTransitionError(f"agent endpoint name is ambiguous: {name}")
        return matches[0]

    def list_tasks(self, status: TaskStatus | None = None) -> list[Task]:
        tasks = self.tasks.values()
        if status is not None:
            tasks = [task for task in tasks if task.status == status]
        return sorted(tasks, key=lambda task: (-task.priority, task.created_at))

    def task_detail(self, task_id: str) -> TaskDetail:
        task = self._task(task_id)
        history = sorted((comment for comment in self.comments.values() if comment.task_id == task_id), key=lambda comment: comment.created_at)
        return TaskDetail(
            task=task,
            parents=[self._task(parent_id) for parent_id in sorted(task.parent_ids)],
            children=[self._task(child_id) for child_id in sorted(task.child_ids)],
            runs=sorted((run for run in self.runs.values() if run.task_id == task_id), key=lambda run: run.started_at),
            comments=history,
            history=history,
            questions=sorted((question for question in self.questions.values() if question.task_id == task_id), key=lambda question: question.created_at),
            artifacts=sorted((artifact for artifact in self.artifacts.values() if artifact.task_id == task_id), key=lambda artifact: artifact.created_at),
            events=[event for event in self.events if event.task_id == task_id],
        )

    def update_task(
        self,
        task_id: str,
        title: str | None = None,
        agent_endpoint_id: str | None = None,
        body: str | None = None,
        priority: int | None = None,
        exclusive: bool | None = None,
    ) -> Task:
        task = self._task(task_id)
        if agent_endpoint_id is not None:
            self._agent_endpoint(agent_endpoint_id)
            task.agent_endpoint_id = agent_endpoint_id
        if title is not None:
            task.title = title
        if body is not None:
            task.body = body
        if priority is not None:
            task.priority = priority
        if exclusive is not None:
            task.exclusive = exclusive
        task.updated_at = utc_now()
        self._event(EventKind.UPDATED, "Task updated", task_id=task.id)
        self._save()
        return task

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

    def scope_task(self, task_id: str) -> Task:
        task = self._task(task_id)
        if task.status != TaskStatus.SCOPING:
            raise InvalidTransitionError(f"cannot scope non-scoping task: {task_id}")
        task.status = TaskStatus.TODO
        task.updated_at = utc_now()
        self._event(EventKind.UPDATED, "Task moved from scoping to todo", task_id=task.id)
        self.recompute_readiness()
        self._save()
        return task

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

    def lease_next(self, runner_id: str, lease_seconds: int = 300, now: datetime | None = None, psk: str | None = None) -> Lease | None:
        now = now or datetime.now(UTC)
        authenticated_runner = self.authenticate_runner(runner_id, psk, now) if psk is not None else None
        self.reclaim_expired_leases(now)
        self.recompute_readiness()

        active_runs = self._active_runs(now)
        if self._exclusive_task_running() or (active_runs and self._ready_exclusive_tasks()):
            return None

        for task in self._ready_tasks_by_priority():
            endpoint = self.agent_endpoints.get(task.agent_endpoint_id)
            if endpoint is None or not endpoint.enabled:
                continue
            if endpoint.runner_id is not None and authenticated_runner is None:
                continue
            if authenticated_runner is not None and endpoint.runner_id != authenticated_runner.id:
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
        task.completed_at = now
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
        task.status = TaskStatus.BLOCKED
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

    def ask_question_from_run(self, run_id: str, body: str, resolves_block: bool = True, block_reason: str | None = None) -> Question:
        run = self._run(run_id)
        if run.status not in {RunStatus.LEASED, RunStatus.RUNNING}:
            raise InvalidTransitionError(f"cannot ask question from terminal run: {run_id}")
        task = self._task(run.task_id)
        now = utc_now()
        reason = block_reason or body
        question = Question(task_id=task.id, run_id=run.id, asked_by=run.runner_id, body=body, resolves_block=resolves_block, created_at=now)
        self.questions[question.id] = question
        self.comments[str(uuid4())] = Comment(task_id=task.id, author=run.runner_id, body=body, created_at=now)
        run.status = RunStatus.BLOCKED
        run.summary = reason
        run.finished_at = now
        task.status = TaskStatus.BLOCKED
        task.updated_at = now
        self._event(
            EventKind.QUESTION_ASKED,
            "Question asked",
            task_id=task.id,
            run_id=run.id,
            metadata={"question_id": question.id, "asked_by": run.runner_id, "resolves_block": resolves_block},
        )
        self._event(EventKind.BLOCKED, "Run blocked", task_id=task.id, run_id=run.id, metadata={"reason": reason})
        self._save()
        return question

    def answer_question(
        self,
        task_id: str,
        question_id: str,
        body: str,
        answered_by: str = "human",
        unblock_if_resolved: bool = True,
    ) -> Question:
        task = self._task(task_id)
        question = self._question(question_id)
        if question.task_id != task.id:
            raise InvalidTransitionError(f"question does not belong to task: {question_id}")
        if question.status != QuestionStatus.OPEN:
            raise InvalidTransitionError(f"question already answered: {question_id}")
        now = utc_now()
        question.status = QuestionStatus.ANSWERED
        question.answer_body = body
        question.answered_by = answered_by
        question.answered_at = now
        self.comments[str(uuid4())] = Comment(task_id=task.id, author=answered_by, body=body, created_at=now)
        self._event(
            EventKind.QUESTION_ANSWERED,
            "Question answered",
            task_id=task.id,
            run_id=question.run_id,
            metadata={"question_id": question.id, "answered_by": answered_by},
        )
        if unblock_if_resolved and question.resolves_block and task.status == TaskStatus.BLOCKED:
            task.status = TaskStatus.TODO
            task.updated_at = now
            self._event(EventKind.READY, "Task unblocked", task_id=task.id, metadata={"question_id": question.id})
            self.recompute_readiness()
        self._save()
        return question

    def unblock_task(self, task_id: str) -> Task:
        task = self._task(task_id)
        if task.status != TaskStatus.BLOCKED:
            raise InvalidTransitionError(f"cannot unblock non-blocked task: {task_id}")
        task.status = TaskStatus.TODO
        task.updated_at = utc_now()
        self._event(EventKind.READY, "Task unblocked", task_id=task.id)
        self.recompute_readiness()
        self._save()
        return task

    def authenticate_runner(self, runner_id: str, psk: str, now: datetime | None = None) -> Runner:
        runner = self._runner(runner_id)
        if not runner.enabled:
            raise InvalidTransitionError(f"runner is disabled: {runner_id}")
        if not hmac.compare_digest(runner.psk_hash, self._hash_psk(psk)):
            raise InvalidTransitionError("runner authentication failed")
        runner.last_seen_at = now or utc_now()
        self._save()
        return runner

    def authenticate_run(self, run_id: str, psk: str | None, now: datetime | None = None) -> Run:
        run = self._run(run_id)
        if run.runner_id not in self.runners:
            return run
        if psk is None:
            raise InvalidTransitionError("runner authentication failed")
        self.authenticate_runner(run.runner_id, psk, now=now)
        return run

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
                task.status = TaskStatus.BLOCKED
                task.updated_at = now
                reclaimed.append(run)
                self._event(EventKind.RECLAIMED, "Expired lease reclaimed", task_id=task.id, run_id=run.id)
        if reclaimed:
            self._save()
        return reclaimed

    def _runner(self, runner_id: str) -> Runner:
        try:
            return self.runners[runner_id]
        except KeyError as error:
            raise NotFoundError(f"runner not found: {runner_id}") from error

    def _public_runner(self, runner: Runner) -> RunnerPublic:
        return RunnerPublic(id=runner.id, name=runner.name, enabled=runner.enabled, created_at=runner.created_at, last_seen_at=runner.last_seen_at)

    @staticmethod
    def _hash_psk(psk: str) -> str:
        return hashlib.sha256(psk.encode("utf-8")).hexdigest()

    def _agent_endpoint(self, endpoint_id: str) -> AgentEndpoint:
        try:
            return self.agent_endpoints[endpoint_id]
        except KeyError as error:
            raise NotFoundError(f"agent endpoint not found: {endpoint_id}") from error

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

    def _question(self, question_id: str) -> Question:
        try:
            return self.questions[question_id]
        except KeyError as error:
            raise NotFoundError(f"question not found: {question_id}") from error

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
