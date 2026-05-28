# Kanban Agent Orchestrator

Kanban-first central orchestrator for remote/OpenAI-compatible agent runners.

This is a deployable MVP: a durable API service, a built-in web board, runner lease endpoints, task lifecycle tracking, comments, markdown artifacts, and event audit trail. It is intentionally small and boring. Distributed orchestration is already cursed; no need to add interpretive dance.

## Features

- Durable JSON-backed state by default.
- Agent endpoints with per-endpoint max concurrency.
- Tasks with priorities, dependency DAGs, and exclusive execution.
- Runner leases with heartbeat, finish, fail, block, and expired lease reclaim.
- Comments and markdown artifacts per task.
- Append-only event log.
- Built-in web UI at `/`.
- OpenAPI docs at `/docs`.
- Dockerfile and Compose for local deployment.
- Pytest, Black, Ruff, and GitHub Actions CI.

## Quick start

```bash
uv sync --dev
uv run kanban-agent-orchestrator --host 127.0.0.1 --port 8080
```

Open:

- Board: http://127.0.0.1:8080/
- API docs: http://127.0.0.1:8080/docs
- Health: http://127.0.0.1:8080/healthz

State is stored at `./data/orchestrator.json` unless `KANBAN_ORCHESTRATOR_DB` is set.

## Docker

```bash
docker compose up --build
```

Then open http://127.0.0.1:8080/.

## API sketch

Human/operator API:

- `GET /api/v1/snapshot`
- `GET /api/v1/stats`
- `GET /api/v1/agent-endpoints`
- `POST /api/v1/agent-endpoints`
- `GET /api/v1/tasks`
- `POST /api/v1/tasks`
- `POST /api/v1/agent-tasks` — agent-friendly creation by assignee name, with optional parent/dependency IDs.
- `GET /api/v1/tasks/{task_id}`
- `POST /api/v1/tasks/{task_id}/unblock`
- `POST /api/v1/tasks/{task_id}/comments`
- `POST /api/v1/tasks/{task_id}/artifacts`
- `POST /api/v1/dependencies`
- `GET /api/v1/events`

Runner API:

- `POST /runner/v1/lease`
- `POST /runner/v1/runs/{run_id}/heartbeat`
- `POST /runner/v1/runs/{run_id}/tasks` — create follow-up tasks from an active run.
- `POST /runner/v1/runs/{run_id}/finish`
- `POST /runner/v1/runs/{run_id}/fail`
- `POST /runner/v1/runs/{run_id}/block`

## Example runner flow

```bash
curl -s -X POST http://127.0.0.1:8080/api/v1/agent-endpoints \
  -H 'content-type: application/json' \
  -d '{"name":"coder","max_concurrency":1}'

curl -s -X POST http://127.0.0.1:8080/runner/v1/lease \
  -H 'content-type: application/json' \
  -d '{"runner_id":"runner-1"}'
```

## Development checks

```bash
uv run black --check .
uv run ruff check .
uv run pytest
```

## Current product limits

- JSON persistence is fine for one service process. Use Postgres before running multiple API replicas.
- No auth yet. Put it behind a trusted network/proxy before inviting the internet to your task board buffet.
- Runner implementation is an API contract, not a full OpenAI-compatible executor yet.
