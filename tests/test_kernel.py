from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from kanban_agent_orchestrator.app import create_app, create_public_app, create_runner_app
from kanban_agent_orchestrator.errors import DependencyCycleError, InvalidTransitionError
from kanban_agent_orchestrator.kernel import OrchestratorKernel
from kanban_agent_orchestrator.models import QuestionStatus, Run, RunStatus, TaskStatus


def test_dependency_gates_child_until_parent_is_done() -> None:
    kernel = OrchestratorKernel()
    endpoint = kernel.create_agent_endpoint("coder")
    parent = kernel.create_task("parent", endpoint.id)
    child = kernel.create_task("child", endpoint.id)

    kernel.add_dependency(parent.id, child.id)

    assert parent.status == TaskStatus.READY
    assert child.status == TaskStatus.TODO

    lease = kernel.lease_next("runner-1")
    assert lease is not None
    assert lease.task.id == parent.id

    kernel.complete_run(lease.run.id, "done")

    assert parent.status == TaskStatus.DONE
    assert child.status == TaskStatus.READY


def test_dependency_cycle_is_rejected() -> None:
    kernel = OrchestratorKernel()
    endpoint = kernel.create_agent_endpoint("coder")
    first = kernel.create_task("first", endpoint.id)
    second = kernel.create_task("second", endpoint.id)

    kernel.add_dependency(first.id, second.id)

    with pytest.raises(DependencyCycleError):
        kernel.add_dependency(second.id, first.id)


def test_lease_respects_agent_max_concurrency() -> None:
    kernel = OrchestratorKernel()
    endpoint = kernel.create_agent_endpoint("coder", max_concurrency=1)
    first = kernel.create_task("first", endpoint.id)
    second = kernel.create_task("second", endpoint.id)

    first_lease = kernel.lease_next("runner-1")
    second_lease = kernel.lease_next("runner-2")

    assert first_lease is not None
    assert first_lease.task.id == first.id
    assert second_lease is None
    assert second.status == TaskStatus.READY

    kernel.complete_run(first_lease.run.id)

    next_lease = kernel.lease_next("runner-2")
    assert next_lease is not None
    assert next_lease.task.id == second.id


def test_exclusive_task_waits_for_running_tasks_then_blocks_new_leases() -> None:
    kernel = OrchestratorKernel()
    endpoint = kernel.create_agent_endpoint("coder", max_concurrency=3)
    normal = kernel.create_task("normal", endpoint.id, priority=10)
    exclusive = kernel.create_task("exclusive", endpoint.id, priority=1, exclusive=True)
    other = kernel.create_task("other", endpoint.id, priority=0)

    normal_lease = kernel.lease_next("runner-1")
    assert normal_lease is not None
    assert normal_lease.task.id == normal.id

    assert kernel.lease_next("runner-2") is None

    kernel.complete_run(normal_lease.run.id)

    exclusive_lease = kernel.lease_next("runner-2")
    assert exclusive_lease is not None
    assert exclusive_lease.task.id == exclusive.id
    assert kernel.lease_next("runner-3") is None

    kernel.complete_run(exclusive_lease.run.id)

    other_lease = kernel.lease_next("runner-3")
    assert other_lease is not None
    assert other_lease.task.id == other.id


def test_expired_lease_is_reclaimed_and_task_can_be_released() -> None:
    kernel = OrchestratorKernel()
    endpoint = kernel.create_agent_endpoint("coder")
    task = kernel.create_task("task", endpoint.id)
    now = datetime(2026, 1, 1, tzinfo=UTC)

    first_lease = kernel.lease_next("runner-1", lease_seconds=10, now=now)
    assert first_lease is not None

    reclaimed = kernel.reclaim_expired_leases(now + timedelta(seconds=11))

    assert reclaimed == [first_lease.run]
    assert first_lease.run.status == RunStatus.FAILED
    assert task.status == TaskStatus.READY

    second_lease = kernel.lease_next("runner-2", now=now + timedelta(seconds=12))
    assert second_lease is not None
    assert second_lease.task.id == task.id


def test_agent_can_create_follow_up_task_by_assignee_name() -> None:
    kernel = OrchestratorKernel()
    reviewer = kernel.create_agent_endpoint("reviewer")
    coder = kernel.create_agent_endpoint("coder")
    parent = kernel.create_task("review", reviewer.id)

    child = kernel.create_task_for_agent(
        title="fix finding",
        assignee="coder",
        body="Patch the bug found during review.",
        parent_id=parent.id,
        priority=5,
        created_by="reviewer-runner",
    )

    assert child.agent_endpoint_id == coder.id
    assert child.priority == 5
    assert child.parent_ids == {parent.id}
    assert child.id in parent.child_ids
    assert child.status == TaskStatus.TODO
    assert any(event.kind == "created" and event.metadata.get("created_by") == "reviewer-runner" for event in kernel.events)


def test_running_agent_can_create_child_task_from_run() -> None:
    kernel = OrchestratorKernel()
    reviewer = kernel.create_agent_endpoint("reviewer")
    coder = kernel.create_agent_endpoint("coder")
    review_task = kernel.create_task("review", reviewer.id)
    lease = kernel.lease_next("reviewer-runner")
    assert lease is not None

    child = kernel.create_task_from_run(
        run_id=lease.run.id,
        title="fix review finding",
        assignee="coder",
        body="Follow-up created by the running review agent.",
    )

    assert child.agent_endpoint_id == coder.id
    assert child.parent_ids == {review_task.id}
    assert child.status == TaskStatus.TODO

    kernel.complete_run(lease.run.id)

    assert child.status == TaskStatus.READY


def test_running_agent_can_ask_question_and_block_task() -> None:
    kernel = OrchestratorKernel()
    endpoint = kernel.create_agent_endpoint("coder")
    task = kernel.create_task("needs input", endpoint.id)
    lease = kernel.lease_next("coder-runner")
    assert lease is not None

    question = kernel.ask_question_from_run(lease.run.id, "Which timeout should I use?", block_reason="Need timeout decision")
    detail = kernel.task_detail(task.id)

    assert question.status == QuestionStatus.OPEN
    assert question.task_id == task.id
    assert question.run_id == lease.run.id
    assert question.asked_by == "coder-runner"
    assert task.status == TaskStatus.BLOCKED
    assert lease.run.status == RunStatus.BLOCKED
    assert detail.questions == [question]
    assert any(comment.body == "Which timeout should I use?" and comment.author == "coder-runner" for comment in detail.comments)
    assert any(event.kind == "question_asked" and event.metadata.get("question_id") == question.id for event in kernel.events)
    assert any(event.kind == "blocked" and event.run_id == lease.run.id for event in kernel.events)


def test_answered_question_unblocks_blocked_task() -> None:
    kernel = OrchestratorKernel()
    endpoint = kernel.create_agent_endpoint("coder")
    task = kernel.create_task("needs input", endpoint.id)
    lease = kernel.lease_next("coder-runner")
    assert lease is not None
    question = kernel.ask_question_from_run(lease.run.id, "Which timeout should I use?")

    answered = kernel.answer_question(task.id, question.id, "Use 30 seconds.", answered_by="ryan")
    detail = kernel.task_detail(task.id)

    assert answered.status == QuestionStatus.ANSWERED
    assert answered.answer_body == "Use 30 seconds."
    assert answered.answered_by == "ryan"
    assert answered.answered_at is not None
    assert task.status == TaskStatus.READY
    assert any(comment.body == "Use 30 seconds." and comment.author == "ryan" for comment in detail.comments)
    assert any(event.kind == "question_answered" and event.metadata.get("question_id") == question.id for event in kernel.events)


def test_answered_question_respects_dependencies_when_unblocking() -> None:
    kernel = OrchestratorKernel()
    endpoint = kernel.create_agent_endpoint("coder", max_concurrency=2)
    parent = kernel.create_task("parent", endpoint.id)
    child = kernel.create_task("child", endpoint.id, parent_ids=[parent.id])
    parent_lease = kernel.lease_next("parent-runner")
    assert parent_lease is not None
    assert parent_lease.task.id == parent.id
    child.status = TaskStatus.RUNNING
    child_run = Run(task_id=child.id, runner_id="child-runner", agent_endpoint_id=endpoint.id)
    kernel.runs[child_run.id] = child_run
    question = kernel.ask_question_from_run(child_run.id, "Should I wait for parent?")

    kernel.answer_question(child.id, question.id, "Yes.")

    assert child.status == TaskStatus.TODO


def test_question_cannot_be_answered_twice() -> None:
    kernel = OrchestratorKernel()
    endpoint = kernel.create_agent_endpoint("coder")
    task = kernel.create_task("needs input", endpoint.id)
    lease = kernel.lease_next("coder-runner")
    assert lease is not None
    question = kernel.ask_question_from_run(lease.run.id, "Which timeout should I use?")

    kernel.answer_question(task.id, question.id, "Use 30 seconds.")

    with pytest.raises(InvalidTransitionError):
        kernel.answer_question(task.id, question.id, "Actually, 60.")


def test_fastapi_minimal_lease_flow() -> None:
    client = TestClient(create_app(OrchestratorKernel()))

    endpoint_response = client.post("/api/v1/agent-endpoints", json={"name": "coder", "max_concurrency": 1})
    assert endpoint_response.status_code == 200
    endpoint_id = endpoint_response.json()["id"]

    task_response = client.post("/api/v1/tasks", json={"title": "task", "agent_endpoint_id": endpoint_id})
    assert task_response.status_code == 200
    task_id = task_response.json()["id"]

    lease_response = client.post("/runner/v1/lease", json={"runner_id": "runner-1"})
    assert lease_response.status_code == 200
    payload = lease_response.json()
    assert payload["task"]["id"] == task_id
    assert payload["run"]["runner_id"] == "runner-1"


def test_public_and_runner_api_surfaces_are_split() -> None:
    kernel = OrchestratorKernel()
    public_client = TestClient(create_public_app(kernel))
    runner_client = TestClient(create_runner_app(kernel))

    endpoint = public_client.post("/api/v1/agent-endpoints", json={"name": "coder", "max_concurrency": 1}).json()
    task = public_client.post("/api/v1/tasks", json={"title": "task", "agent_endpoint_id": endpoint["id"]}).json()

    public_runner_response = public_client.post("/runner/v1/lease", json={"runner_id": "runner-1"})
    runner_public_response = runner_client.get("/api/v1/snapshot")
    runner_docs_response = runner_client.get("/docs")
    lease_response = runner_client.post("/runner/v1/lease", json={"runner_id": "runner-1"})

    assert public_runner_response.status_code == 404
    assert runner_public_response.status_code == 404
    assert runner_docs_response.status_code == 404
    assert lease_response.status_code == 200
    assert lease_response.json()["task"]["id"] == task["id"]


def test_runner_registration_psk_and_assigned_backend_lease_flow() -> None:
    client = TestClient(create_app(OrchestratorKernel()))

    runner_result = client.post("/api/v1/runners", json={"name": "jetson"}).json()
    runner = runner_result["runner"]
    psk = runner_result["psk"]
    backend = client.post("/api/v1/agent-endpoints", json={"name": "coder", "runner_id": runner["id"], "max_concurrency": 1}).json()
    task = client.post("/api/v1/tasks", json={"title": "task", "agent_endpoint_id": backend["id"]}).json()

    unauthenticated = client.post("/runner/v1/lease", json={"runner_id": runner["id"]})
    wrong_psk = client.post("/runner/v1/lease", headers={"Authorization": "Bearer wrong"}, json={"runner_id": runner["id"]})
    after_wrong_psk = client.get("/api/v1/snapshot").json()
    lease = client.post("/runner/v1/lease", headers={"Authorization": f"Bearer {psk}"}, json={"runner_id": runner["id"]})
    snapshot = client.get("/api/v1/snapshot").json()

    assert psk.startswith("kanban_rnr_")
    assert "psk_hash" not in runner
    assert unauthenticated.json() is None
    assert wrong_psk.status_code == 400
    assert after_wrong_psk["runners"][0]["last_seen_at"] is None
    assert lease.status_code == 200
    assert lease.json()["task"]["id"] == task["id"]
    assert snapshot["runners"][0]["last_seen_at"] is not None


def test_delete_runner_unassigns_and_disables_backends() -> None:
    client = TestClient(create_app(OrchestratorKernel()))

    runner_result = client.post("/api/v1/runners", json={"name": "jetson"}).json()
    runner = runner_result["runner"]
    backend = client.post("/api/v1/agent-endpoints", json={"name": "coder", "runner_id": runner["id"], "max_concurrency": 1}).json()

    response = client.delete(f"/api/v1/runners/{runner['id']}")
    snapshot = client.get("/api/v1/snapshot").json()

    assert response.status_code == 204
    assert snapshot["runners"] == []
    assert snapshot["agent_endpoints"][0]["id"] == backend["id"]
    assert snapshot["agent_endpoints"][0]["runner_id"] is None
    assert snapshot["agent_endpoints"][0]["enabled"] is False
    assert snapshot["events"][-1]["kind"] == "deleted"


def test_delete_runner_with_active_run_is_rejected() -> None:
    client = TestClient(create_app(OrchestratorKernel()))

    runner_result = client.post("/api/v1/runners", json={"name": "jetson"}).json()
    runner = runner_result["runner"]
    psk = runner_result["psk"]
    backend = client.post("/api/v1/agent-endpoints", json={"name": "coder", "runner_id": runner["id"], "max_concurrency": 1}).json()
    client.post("/api/v1/tasks", json={"title": "task", "agent_endpoint_id": backend["id"]}).json()
    lease = client.post("/runner/v1/lease", headers={"Authorization": f"Bearer {psk}"}, json={"runner_id": runner["id"]})

    response = client.delete(f"/api/v1/runners/{runner['id']}")
    snapshot = client.get("/api/v1/snapshot").json()

    assert lease.status_code == 200
    assert response.status_code == 400
    assert snapshot["runners"][0]["id"] == runner["id"]


def test_runner_run_actions_require_registered_runner_psk() -> None:
    client = TestClient(create_app(OrchestratorKernel()))

    runner_result = client.post("/api/v1/runners", json={"name": "jetson"}).json()
    runner = runner_result["runner"]
    psk = runner_result["psk"]
    backend = client.post("/api/v1/agent-endpoints", json={"name": "coder", "runner_id": runner["id"], "max_concurrency": 1}).json()
    client.post("/api/v1/tasks", json={"title": "task", "agent_endpoint_id": backend["id"]}).json()
    lease = client.post("/runner/v1/lease", headers={"Authorization": f"Bearer {psk}"}, json={"runner_id": runner["id"]}).json()
    run_id = lease["run"]["id"]

    unauthenticated = client.post(f"/runner/v1/runs/{run_id}/heartbeat", json={})
    wrong_psk = client.post(f"/runner/v1/runs/{run_id}/heartbeat", headers={"Authorization": "Bearer wrong"}, json={})
    valid = client.post(f"/runner/v1/runs/{run_id}/heartbeat", headers={"Authorization": f"Bearer {psk}"}, json={})

    assert unauthenticated.status_code == 400
    assert wrong_psk.status_code == 400
    assert valid.status_code == 200
    assert valid.json()["id"] == run_id
