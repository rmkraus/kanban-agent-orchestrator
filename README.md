# Kanban Agent Orchestrator

Kanban-first central orchestrator for remote/OpenAI-compatible agent runners.

This is a deployable MVP: a durable API service, a built-in React board, runner lease endpoints, task lifecycle tracking, comments, structured questions, markdown artifacts, and event audit trail. It is intentionally small and boring. Distributed orchestration is already cursed; no need to add interpretive dance.

## Features

- Durable JSON-backed state by default.
- Registered runners with one-time PSKs and `last_seen_at` heartbeat tracking.
- Backends with per-backend max concurrency and optional runner assignment.
- Tasks with priorities, dependency DAGs, and exclusive execution.
- Authenticated runner leases with heartbeat, finish, fail, block, question, and expired lease reclaim.
- Comments, structured questions, and markdown artifacts per task.
- Append-only event log.
- Built-in React web UI at `/`.
- OpenAPI docs at `/docs`.
- Dockerfile and Compose for local deployment.
- Pytest, Black, Ruff, Prettier, ESLint, TypeScript, and GitHub Actions CI.

## Quick start

```bash
uv sync --dev
uv run kanban-agent-orchestrator --host 127.0.0.1 --port 8080 --runner-port 8082
```

Open:

- Public API/UI: http://127.0.0.1:8080/
- Runner API: http://127.0.0.1:8082/
- API docs: http://127.0.0.1:8080/docs
- Health: http://127.0.0.1:8080/healthz and http://127.0.0.1:8082/healthz

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
- `GET /api/v1/runners`
- `POST /api/v1/runners` — returns the raw runner PSK once; only the hash is stored.
- `PATCH /api/v1/runners/{runner_id}`
- `GET /api/v1/agent-endpoints`
- `POST /api/v1/agent-endpoints`
- `PATCH /api/v1/agent-endpoints/{endpoint_id}`
- `GET /api/v1/tasks`
- `POST /api/v1/tasks`
- `POST /api/v1/agent-tasks` — agent-friendly creation by assignee name, with optional parent/dependency IDs.
- `GET /api/v1/tasks/{task_id}`
- `POST /api/v1/tasks/{task_id}/unblock`
- `POST /api/v1/tasks/{task_id}/questions/{question_id}/answer` — answer a blocked agent question and optionally unblock the task.
- `POST /api/v1/tasks/{task_id}/comments`
- `POST /api/v1/tasks/{task_id}/artifacts`
- `POST /api/v1/dependencies`
- `GET /api/v1/events`

## Runner-only API

Runner endpoints are served on the runner-only port, `8082` by default, so the public UI/API port can sit behind basic auth, OAuth, or whatever bouncer you hire. The runner port exposes only `/healthz` and `/runner/v1/*`; it does not serve the frontend, public `/api/v1/*`, or API docs.

- `POST /runner/v1/lease`
- `POST /runner/v1/runs/{run_id}/heartbeat`
- `POST /runner/v1/runs/{run_id}/tasks` — create follow-up tasks from an active run.
- `POST /runner/v1/runs/{run_id}/questions` — ask a task-chat question and block the run/task until answered.
- `POST /runner/v1/runs/{run_id}/finish`
- `POST /runner/v1/runs/{run_id}/fail`
- `POST /runner/v1/runs/{run_id}/block`

## Runner install flow

Create a runner, then store its one-time PSK in a root-readable env file for the systemd service. Do not put the PSK in `ExecStart`; process args are not a vault, despite their strong confidence.

```bash
# 1. Create a runner. Save the returned `runner.id` and `psk` locally.
curl -s -X POST http://127.0.0.1:8080/api/v1/runners \
  -H 'content-type: application/json' \
  -d '{"name":"spark-0"}'

# 2. Install the persistent runner service. Pass the PSK on stdin.
printf '%s' "$KANBAN_RUNNER_PSK" | sudo kanban-runner install-systemd \
  --server http://127.0.0.1:8082 \
  --runner-id "$KANBAN_RUNNER_ID" \
  --name kanban-runner-spark-0 \
  --psk-stdin
```

Runner lease and run-control calls use the runner-only port and `Authorization: Bearer <runner-psk>`. Invalid PSKs are rejected and do not update `last_seen_at`.

## Example runner flow

```bash
curl -s -X POST http://127.0.0.1:8080/api/v1/runners \
  -H 'content-type: application/json' \
  -d '{"name":"local-runner"}'

curl -s -X POST http://127.0.0.1:8080/api/v1/agent-endpoints \
  -H 'content-type: application/json' \
  -d '{"name":"coder","max_concurrency":1,"runner_id":"'$KANBAN_RUNNER_ID'"}'

curl -s -X POST http://127.0.0.1:8082/runner/v1/lease \
  -H 'content-type: application/json' \
-H "authorization: Bearer $KANBAN_RUNNER_PSK"
  -d '{"runner_id":"'$KANBAN_RUNNER_ID'"}'
```

## Blocking questions

Agents can ask a question and block their current task in one call:

```bash
curl -s -X POST http://127.0.0.1:8082/runner/v1/runs/$RUN_ID/questions \
  -H 'content-type: application/json' \
-H "authorization: Bearer $KANBAN_RUNNER_PSK"
  -d '{"body":"Which API timeout should I use?","resolves_block":true}'
```

That creates a structured question, adds it to the task chat, marks the run `blocked`, and marks the task `blocked`.

Answering the question can resolve the block:

```bash
curl -s -X POST http://127.0.0.1:8080/api/v1/tasks/$TASK_ID/questions/$QUESTION_ID/answer \
  -H 'content-type: application/json' \
  -d '{"body":"Use 30 seconds.","answered_by":"ryan"}'
```

If the question has `resolves_block=true`, the task returns to `ready` or `todo` depending on parent dependencies. The blocked run stays terminal; a future lease picks the task back up with the answered context in task detail.

## Development checks

```bash
uv run black --check .
uv run ruff check .
uv run pytest
npm ci --prefix web
npm run format:check --prefix web
npm run lint --prefix web
npm run typecheck --prefix web
npm run build --prefix web
```

## Current product limits

- JSON persistence is fine for one service process. Use Postgres before running multiple API replicas.
- No auth yet. Put it behind a trusted network/proxy before inviting the internet to your task board buffet.
- Runner implementation is an API contract plus a minimal polling CLI, not a full OpenAI-compatible executor yet.
