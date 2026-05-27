from pathlib import Path

from fastapi.testclient import TestClient

from kanban_agent_orchestrator.app import create_app
from kanban_agent_orchestrator.kernel import OrchestratorKernel
from kanban_agent_orchestrator.models import RunStatus, TaskStatus


def make_client() -> TestClient:
    return TestClient(create_app(OrchestratorKernel()))


def test_persistent_kernel_round_trips_state(tmp_path: Path) -> None:
    db_path = tmp_path / "orchestrator.json"
    kernel = OrchestratorKernel.persistent(db_path)
    endpoint = kernel.create_agent_endpoint("coder", max_concurrency=2)
    task = kernel.create_task("persist me", endpoint.id, body="durable-ish JSON is still better than vibes")
    comment = kernel.add_comment(task.id, "hello", author="tester")
    artifact = kernel.add_artifact(task.id, "result.md", "# Result")

    reloaded = OrchestratorKernel.persistent(db_path)

    assert reloaded.agent_endpoints[endpoint.id].name == "coder"
    assert reloaded.tasks[task.id].title == "persist me"
    assert reloaded.comments[comment.id].body == "hello"
    assert reloaded.artifacts[artifact.id].filename == "result.md"
    assert reloaded.events


def test_api_lifecycle_comments_artifacts_and_events() -> None:
    client = make_client()
    endpoint = client.post("/api/v1/agent-endpoints", json={"name": "coder", "max_concurrency": 1}).json()
    task = client.post("/api/v1/tasks", json={"title": "ship it", "agent_endpoint_id": endpoint["id"], "body": "please"}).json()

    comment = client.post(f"/api/v1/tasks/{task['id']}/comments", json={"body": "working", "author": "runner"})
    artifact = client.post(f"/api/v1/tasks/{task['id']}/artifacts", json={"filename": "handoff.md", "content_markdown": "done"})
    lease = client.post("/runner/v1/lease", json={"runner_id": "runner-1"}).json()
    heartbeat = client.post(f"/runner/v1/runs/{lease['run']['id']}/heartbeat", json={"lease_seconds": 60})
    finish = client.post(f"/runner/v1/runs/{lease['run']['id']}/finish", json={"summary": "complete"})
    detail = client.get(f"/api/v1/tasks/{task['id']}").json()
    events = client.get("/api/v1/events").json()

    assert comment.status_code == 200
    assert artifact.status_code == 200
    assert heartbeat.status_code == 200
    assert finish.status_code == 200
    assert detail["task"]["status"] == TaskStatus.DONE
    assert detail["comments"][0]["body"] == "working"
    assert detail["artifacts"][0]["filename"] == "handoff.md"
    assert any(event["kind"] == "completed" for event in events)


def test_api_blocks_and_unblocks_task() -> None:
    client = make_client()
    endpoint = client.post("/api/v1/agent-endpoints", json={"name": "coder"}).json()
    task = client.post("/api/v1/tasks", json={"title": "needs input", "agent_endpoint_id": endpoint["id"]}).json()
    lease = client.post("/runner/v1/lease", json={"runner_id": "runner-1"}).json()

    blocked = client.post(f"/runner/v1/runs/{lease['run']['id']}/block", json={"reason": "need human"}).json()
    unblocked = client.post(f"/api/v1/tasks/{task['id']}/unblock").json()

    assert blocked["status"] == RunStatus.BLOCKED
    assert unblocked["status"] == TaskStatus.READY


def test_board_ui_is_served() -> None:
    client = make_client()
    response = client.get("/")

    assert response.status_code == 200
    assert "Kanban Agent Orchestrator" in response.text
    assert "Create Task" in response.text
