from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from kanban_agent_orchestrator.app import create_app
from kanban_agent_orchestrator.errors import DependencyCycleError
from kanban_agent_orchestrator.kernel import OrchestratorKernel
from kanban_agent_orchestrator.models import RunStatus, TaskStatus


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
