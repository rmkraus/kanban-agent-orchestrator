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
    updated = client.patch(f"/api/v1/tasks/{task['id']}", json={"title": "ship it harder", "body": "# please\n\n- now", "priority": 3, "exclusive": True})

    comment = client.post(f"/api/v1/tasks/{task['id']}/comments", json={"body": "working", "author": "runner"})
    artifact = client.post(f"/api/v1/tasks/{task['id']}/artifacts", json={"filename": "handoff.md", "content_markdown": "done"})
    lease = client.post("/runner/v1/lease", json={"runner_id": "runner-1"}).json()
    heartbeat = client.post(f"/runner/v1/runs/{lease['run']['id']}/heartbeat", json={"lease_seconds": 60})
    finish = client.post(f"/runner/v1/runs/{lease['run']['id']}/finish", json={"summary": "complete"})
    detail = client.get(f"/api/v1/tasks/{task['id']}").json()
    events = client.get("/api/v1/events").json()

    assert updated.status_code == 200
    assert updated.json()["id"] == task["id"]
    assert updated.json()["context_id"] == task["context_id"]
    assert updated.json()["title"] == "ship it harder"
    assert updated.json()["body"] == "# please\n\n- now"
    assert updated.json()["priority"] == 3
    assert updated.json()["exclusive"] is True
    assert comment.status_code == 200
    assert artifact.status_code == 200
    assert heartbeat.status_code == 200
    assert finish.status_code == 200
    assert detail["task"]["status"] == TaskStatus.DONE
    assert detail["task"]["context_id"] == task["context_id"]
    assert detail["comments"][0]["body"] == "working"
    assert detail["history"] == detail["comments"]
    assert detail["artifacts"][0]["filename"] == "handoff.md"
    assert any(event["kind"] == "completed" for event in events)


def test_agent_task_api_creates_task_by_assignee_and_dependencies() -> None:
    client = make_client()
    reviewer = client.post("/api/v1/agent-endpoints", json={"name": "reviewer"}).json()
    client.post("/api/v1/agent-endpoints", json={"name": "coder"}).json()
    parent = client.post("/api/v1/tasks", json={"title": "review", "agent_endpoint_id": reviewer["id"]}).json()

    child = client.post(
        "/api/v1/agent-tasks",
        json={
            "title": "fix review finding",
            "assignee": "coder",
            "body": "Follow-up from review.",
            "parent_id": parent["id"],
            "created_by": "reviewer-agent",
        },
    )
    detail = client.get(f"/api/v1/tasks/{parent['id']}").json()

    assert child.status_code == 200
    assert child.json()["parent_ids"] == [parent["id"]]
    assert detail["children"][0]["id"] == child.json()["id"]


def test_runner_can_create_child_task_from_active_run() -> None:
    client = make_client()
    reviewer = client.post("/api/v1/agent-endpoints", json={"name": "reviewer"}).json()
    client.post("/api/v1/agent-endpoints", json={"name": "coder"}).json()
    parent = client.post("/api/v1/tasks", json={"title": "review", "agent_endpoint_id": reviewer["id"]}).json()
    lease = client.post("/runner/v1/lease", json={"runner_id": "reviewer-runner"}).json()

    child = client.post(
        f"/runner/v1/runs/{lease['run']['id']}/tasks",
        json={"title": "fix review finding", "assignee": "coder", "body": "Created by active run."},
    )

    assert child.status_code == 200
    assert child.json()["parent_ids"] == [parent["id"]]


def test_runner_question_blocks_task_and_answer_unblocks_it() -> None:
    client = make_client()
    endpoint = client.post("/api/v1/agent-endpoints", json={"name": "coder"}).json()
    task = client.post("/api/v1/tasks", json={"title": "needs input", "agent_endpoint_id": endpoint["id"]}).json()
    lease = client.post("/runner/v1/lease", json={"runner_id": "runner-1"}).json()

    question_response = client.post(
        f"/runner/v1/runs/{lease['run']['id']}/questions",
        json={"body": "Which timeout should I use?", "resolves_block": True},
    )
    question = question_response.json()
    blocked_detail = client.get(f"/api/v1/tasks/{task['id']}").json()
    answer_response = client.post(
        f"/api/v1/tasks/{task['id']}/questions/{question['id']}/answer",
        json={"body": "Use 30 seconds.", "answered_by": "ryan"},
    )
    answered_detail = client.get(f"/api/v1/tasks/{task['id']}").json()

    assert question_response.status_code == 200
    assert question["status"] == "open"
    assert blocked_detail["task"]["status"] == TaskStatus.BLOCKED
    assert blocked_detail["questions"][0]["id"] == question["id"]
    assert blocked_detail["comments"][0]["body"] == "Which timeout should I use?"
    assert answer_response.status_code == 200
    assert answer_response.json()["status"] == "answered"
    assert answered_detail["task"]["status"] == TaskStatus.READY
    assert answered_detail["questions"][0]["answer_body"] == "Use 30 seconds."
    assert any(comment["body"] == "Use 30 seconds." for comment in answered_detail["comments"])


def test_answering_non_task_question_is_rejected() -> None:
    client = make_client()
    endpoint = client.post("/api/v1/agent-endpoints", json={"name": "coder", "max_concurrency": 2}).json()
    first = client.post("/api/v1/tasks", json={"title": "first", "agent_endpoint_id": endpoint["id"]}).json()
    second = client.post("/api/v1/tasks", json={"title": "second", "agent_endpoint_id": endpoint["id"]}).json()
    lease = client.post("/runner/v1/lease", json={"runner_id": "runner-1"}).json()
    question = client.post(f"/runner/v1/runs/{lease['run']['id']}/questions", json={"body": "Question for first."}).json()

    response = client.post(
        f"/api/v1/tasks/{second['id']}/questions/{question['id']}/answer",
        json={"body": "Wrong task."},
    )

    assert first["id"] != second["id"]
    assert response.status_code == 400


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
    assert 'id="root"' in response.text
