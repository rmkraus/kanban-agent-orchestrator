# Blocking Questions and UI Implementation Plan

> **For Hermes:** Plan only for now. When executing this repo, push completed commits directly to `main` per Ryan’s current repo-specific preference.

**Goal:** Let agents block tasks with explicit questions in the task chat, let human answers resolve/unblock those tasks, and replace the current inline board with a polished React UI.

**Architecture:** Extend the existing FastAPI/Pydantic kernel with structured task questions that are also mirrored into the comment/chat stream. Add runner-scoped endpoints so agents can ask-and-block in one safe operation, and human/operator endpoints to answer questions and optionally unblock the task. Build a small Vite React TypeScript frontend served by FastAPI from packaged static assets.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, pytest, Black/Ruff, React, TypeScript, Vite, npm, Docker Compose.

---

## Current Context

- Repo: `/home/nvidia/src/kanban-agent-orchestrator`
- Current backend already supports:
  - `TaskStatus.BLOCKED`
  - `RunStatus.BLOCKED`
  - `OrchestratorKernel.block_run(run_id, reason)`
  - `OrchestratorKernel.unblock_task(task_id)`
  - task comments through `add_comment(task_id, body, author)`
  - APIs:
    - `POST /runner/v1/runs/{run_id}/block`
    - `POST /api/v1/tasks/{task_id}/unblock`
    - `POST /api/v1/tasks/{task_id}/comments`
- Current gap: comments are unstructured; there is no first-class “question”, no answer state, and no automatic “answered question resolves block” flow.
- Current UI is an inline `BOARD_HTML` string in `src/kanban_agent_orchestrator/app.py`; useful, but it smells like a demo stapled to a server. Because it is.

## Proposed Behavior

### Agent flow

1. Agent leases a task.
2. Agent needs human input.
3. Agent calls one runner endpoint to ask the question and block the run/task:
   - `POST /runner/v1/runs/{run_id}/questions`
4. Kernel:
   - creates a `Question`
   - creates a task chat `Comment` from the agent
   - marks the run `blocked`
   - marks the task `blocked`
   - records events

### Human/operator flow

1. Human opens the blocked task in the UI.
2. Human answers the open question in the task chat.
3. UI calls:
   - `POST /api/v1/tasks/{task_id}/questions/{question_id}/answer`
4. Kernel:
   - stores answer fields on the question
   - creates an answer `Comment`
   - records event
   - if `resolves_block=true` and task is blocked, unblocks the task to `ready` or `todo` based on dependencies
5. Dispatcher can lease the task again; the next run sees the question and answer in task detail.

## Data Model Changes

### Modify `src/kanban_agent_orchestrator/models.py`

Add question status enum:

```python
class QuestionStatus(StrEnum):
    OPEN = "open"
    ANSWERED = "answered"
```

Add event kinds:

```python
QUESTION_ASKED = "question_asked"
QUESTION_ANSWERED = "question_answered"
```

Add model:

```python
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
```

Update `TaskDetail`:

```python
questions: list[Question]
```

Update `Snapshot`:

```python
questions: list[Question] = Field(default_factory=list)
```

## Kernel Changes

### Modify `src/kanban_agent_orchestrator/kernel.py`

#### Initialization and snapshot

- Add `self.questions: dict[str, Question]`
- Include questions in `snapshot()`.
- Include task questions in `task_detail()` sorted by creation time.

#### Add `ask_question_from_run(...)`

```python
def ask_question_from_run(self, run_id: str, body: str, resolves_block: bool = True, block_reason: str | None = None) -> Question:
```

Rules:

- Run must be `LEASED` or `RUNNING`.
- Create `Question` with:
  - `task_id=run.task_id`
  - `run_id=run.id`
  - `asked_by=run.runner_id`
  - `body=body`
  - `resolves_block=resolves_block`
- Add a `Comment` to the task chat from `run.runner_id`:
  - body can be exactly the question text, or prefixed with `Question: ...`
- Mark run blocked:
  - `run.status = RunStatus.BLOCKED`
  - `run.summary = block_reason or body`
  - `run.finished_at = now`
- Mark task blocked:
  - `task.status = TaskStatus.BLOCKED`
  - `task.updated_at = now`
- Emit events:
  - `QUESTION_ASKED` with `question_id`, `resolves_block`
  - `BLOCKED` with `reason`
- Save and return the question.

This is deliberately one endpoint/method so agents do not have to do the “comment, then block, but don’t forget the run is terminal now” dance. Dancing is for humans, not orchestration state machines.

#### Add `answer_question(...)`

```python
def answer_question(
    self,
    task_id: str,
    question_id: str,
    body: str,
    answered_by: str = "human",
    unblock_if_resolved: bool = True,
) -> Question:
```

Rules:

- Task must exist.
- Question must exist and belong to the task.
- Question must be `OPEN`; reject double-answering with `InvalidTransitionError`.
- Set:
  - `status=QuestionStatus.ANSWERED`
  - `answer_body=body`
  - `answered_by=answered_by`
  - `answered_at=utc_now()`
- Add a `Comment` to the task chat from `answered_by` with the answer body.
- Emit `QUESTION_ANSWERED` event with `question_id` and `answered_by`.
- If `unblock_if_resolved and question.resolves_block and task.status == TaskStatus.BLOCKED`, call or inline the same transition as `unblock_task`.
- Return the updated question.

#### Helper methods

Add private helper:

```python
def _question(self, question_id: str) -> Question:
```

Optional but useful helper:

```python
def _unblock_task_after_answer(self, task: Task) -> None:
```

Keep this small. No “workflow engine”. No blockchain for comments. Nobody asked for Jira with extra suffering.

## API Changes

### Modify `src/kanban_agent_orchestrator/app.py`

Add imports for `Question` if using explicit response models.

Add request models:

```python
class RunQuestionCreate(BaseModel):
    body: str
    resolves_block: bool = True
    block_reason: str | None = None


class QuestionAnswerCreate(BaseModel):
    body: str
    answered_by: str = "human"
    unblock_if_resolved: bool = True
```

Add route:

```python
@app.post("/runner/v1/runs/{run_id}/questions", response_model=Question)
def ask_question_from_run(run_id: str, payload: RunQuestionCreate) -> Question:
    ...
```

Add route:

```python
@app.post("/api/v1/tasks/{task_id}/questions/{question_id}/answer", response_model=Question)
def answer_question(task_id: str, question_id: str, payload: QuestionAnswerCreate) -> Question:
    ...
```

Update `task_detail` response naturally through `TaskDetail.questions`.

## Backend Tests

### Modify `tests/test_kernel.py`

Add test: `test_running_agent_can_ask_question_and_block_task`

Expected assertions:

- Create endpoint/task.
- Lease task.
- Call `kernel.ask_question_from_run(...)`.
- Question is `open`.
- Question task/run IDs match.
- Task status is `BLOCKED`.
- Run status is `BLOCKED`.
- Task detail includes question.
- Task detail comments include the question text.
- Events include `question_asked` and `blocked`.

Add test: `test_answered_question_unblocks_blocked_task`

Expected assertions:

- Create endpoint/task, lease, ask question.
- Call `kernel.answer_question(...)`.
- Question is `answered`.
- Answer fields are populated.
- Task becomes `READY` when it has no unfinished parents.
- Comments include the answer.
- Events include `question_answered` and `ready`/unblocked event.

Add test: `test_answered_question_respects_dependencies_when_unblocking`

Expected assertions:

- Parent and child task exist; child depends on parent.
- Child is blocked via question/run.
- Answer question.
- Child becomes `TODO`, not `READY`, because parent is not done.

Add test: `test_question_cannot_be_answered_twice`

Expected assertions:

- Second answer raises `InvalidTransitionError`.

### Modify `tests/test_product.py`

Add API test: `test_runner_question_blocks_task_and_answer_unblocks_it`

Flow:

1. Create endpoint.
2. Create task.
3. Lease task.
4. POST `/runner/v1/runs/{run_id}/questions`.
5. GET `/api/v1/tasks/{task_id}` and assert:
   - task is `blocked`
   - questions list has open question
   - comments include question
6. POST `/api/v1/tasks/{task_id}/questions/{question_id}/answer`.
7. GET detail again and assert:
   - question answered
   - task ready
   - comments include answer

Add API test: `test_answering_non_task_question_is_rejected`

- Create two tasks/questions.
- Try answering question through wrong task ID.
- Expect 400 or 404 depending chosen error mapping; prefer 400 `InvalidTransitionError`.

## Frontend Plan

### Add React/Vite app under `web/`

Create:

- `web/package.json`
- `web/package-lock.json`
- `web/index.html`
- `web/tsconfig.json`
- `web/tsconfig.node.json`
- `web/vite.config.ts`
- `web/src/main.tsx`
- `web/src/App.tsx`
- `web/src/api.ts`
- `web/src/types.ts`
- `web/src/styles.css`

Use npm because there is no existing JS package manager or lockfile.

Suggested dependencies:

- runtime:
  - `@vitejs/plugin-react` is dev-only; React runtime is `react`, `react-dom`
- dev:
  - `typescript`
  - `vite`
  - `@vitejs/plugin-react`
  - `eslint`
  - `@eslint/js`
  - `typescript-eslint`
  - `globals`
  - `prettier`

Keep UI dependencies minimal. No component library unless Ryan asks. A component library would be a great way to ship 200KB of sameness and regret.

### UI layout

Build a clean product-style UI, not the usual generated “purple gradient glass card spaceship dashboard”.

Design rules:

- Use restrained neutral palette with one accent color.
- Avoid emoji status labels.
- Avoid rainbow gradients, glassmorphism, giant drop shadows, and “AI SaaS landing page” nonsense.
- Use compact cards and readable typography.
- Use deliberate whitespace.
- Make blocked tasks visibly distinct but not clown-shoes red everywhere.

Main layout:

- Top bar:
  - product name: `Kanban`
  - refresh button
  - small health/stats summary
- Left sidebar:
  - endpoints list
  - create endpoint form
  - create task form
- Main board:
  - columns: `todo`, `ready`, `running`, `blocked`, `done`
  - each card shows title, assignee, priority, parent/child count, question count
- Right detail drawer/panel:
  - selected task title/body/status
  - metadata
  - parent/child IDs
  - questions section
  - chat/comments section
  - answer form for open questions
  - unblock button for manual escape hatch

### API client behavior

In `web/src/api.ts`, implement typed helpers:

- `getSnapshot()` → `/api/v1/snapshot`
- `getStats()` → `/api/v1/stats`
- `getTaskDetail(taskId)` → `/api/v1/tasks/{task_id}`
- `createEndpoint(payload)`
- `createTask(payload)`
- `addComment(taskId, payload)`
- `answerQuestion(taskId, questionId, payload)`
- `unblockTask(taskId)`

Use simple `fetch`; no React Query unless needed later.

### Serving frontend from FastAPI

Modify `src/kanban_agent_orchestrator/app.py`:

- Remove or stop using `BOARD_HTML`.
- Mount static assets from package directory, e.g.:

```python
from pathlib import Path
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

STATIC_DIR = Path(__file__).parent / "static"

if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

@app.get("/", response_class=HTMLResponse)
def board() -> FileResponse | str:
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return "<h1>Kanban Agent Orchestrator</h1><p>Frontend build not found.</p>"
```

- Keep API routes under `/api/v1` and `/runner/v1` so frontend routing stays simple.
- Do not add SPA routing unless needed.

### Packaging static files

Modify `pyproject.toml` for `uv_build` package data if needed. Verify built static files under:

- `src/kanban_agent_orchestrator/static/index.html`
- `src/kanban_agent_orchestrator/static/assets/...`

If `uv_build` does not include package data automatically, add the minimal package-data configuration supported by `uv_build`, or switch to placing static files inside package in a way the build backend includes. Verify with Docker build, not vibes.

### Frontend build command

Add root-level helper scripts only if they stay simple. Options:

- Keep JS scripts in `web/package.json` only:
  - `npm run format`
  - `npm run format:check`
  - `npm run lint`
  - `npm run typecheck`
  - `npm run build`
- Add README instructions.

No need to make Python `uv run` orchestrate npm yet unless CI/Docker need it.

## Docker Changes

### Modify `Dockerfile`

Use a frontend build stage:

```dockerfile
FROM node:22-slim AS frontend
WORKDIR /web
COPY web/package*.json ./
RUN npm ci
COPY web ./
RUN npm run build

FROM python:3.12-slim
...
COPY --from=frontend /web/dist ./src/kanban_agent_orchestrator/static
RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev
```

Important ordering:

- Ensure static files exist before `uv sync` builds/installs the package, if package data is included at build time.
- If package install does not include source static files correctly, adjust copy/install order and verify in the live container.

## CI Changes

### Modify `.github/workflows/ci.yml`

Add Node setup and frontend checks:

- `actions/setup-node@v4` with Node 22 and npm cache pointing at `web/package-lock.json`
- `npm ci --prefix web`
- `npm run format:check --prefix web`
- `npm run lint --prefix web`
- `npm run typecheck --prefix web`
- `npm run build --prefix web`

Keep existing Python checks:

- `uv run black --check .`
- `uv run ruff check .`
- `uv run pytest`

## README Updates

### Modify `README.md`

Document:

- Agent asks and blocks:

```bash
curl -X POST http://127.0.0.1:8080/runner/v1/runs/$RUN_ID/questions \
  -H 'Content-Type: application/json' \
  -d '{"body":"Which API timeout should I use?", "resolves_block":true}'
```

- Human answers and resolves:

```bash
curl -X POST http://127.0.0.1:8080/api/v1/tasks/$TASK_ID/questions/$QUESTION_ID/answer \
  -H 'Content-Type: application/json' \
  -d '{"body":"Use 30 seconds.", "answered_by":"ryan"}'
```

- UI:
  - `docker compose up --build`
  - open `http://127.0.0.1:8080/`

- Development:
  - backend: `uv run ...`
  - frontend: `npm ci --prefix web`, `npm run build --prefix web`

## Step-by-Step Execution Plan

### Task 1: Sync local `main`

**Objective:** Work directly on `main` as requested.

**Commands:**

```bash
cd /home/nvidia/src/kanban-agent-orchestrator
git checkout main
git pull origin main
```

If there is an open PR branch with unmerged desired changes, merge or cherry-pick only if needed after checking current `main`. Do not blindly duplicate work.

### Task 2: Add question models

**Files:**

- Modify: `src/kanban_agent_orchestrator/models.py`

**Steps:**

1. Add `QuestionStatus`.
2. Add question event kinds.
3. Add `Question` model.
4. Update `TaskDetail` and `Snapshot`.
5. Run targeted import/syntax check via tests after kernel wiring, not yet.

### Task 3: Add kernel question lifecycle

**Files:**

- Modify: `src/kanban_agent_orchestrator/kernel.py`

**Steps:**

1. Import `Question` and `QuestionStatus`.
2. Initialize `self.questions` from snapshot.
3. Include questions in `snapshot()`.
4. Include task questions in `task_detail()`.
5. Add `_question()` helper.
6. Add `ask_question_from_run()`.
7. Add `answer_question()`.
8. Keep transitions consistent with existing `block_run()` and `unblock_task()`.

### Task 4: Add kernel tests

**Files:**

- Modify: `tests/test_kernel.py`

**Tests:**

- `test_running_agent_can_ask_question_and_block_task`
- `test_answered_question_unblocks_blocked_task`
- `test_answered_question_respects_dependencies_when_unblocking`
- `test_question_cannot_be_answered_twice`

**Command:**

```bash
uv run pytest tests/test_kernel.py -q
```

Expected: new tests pass after implementation.

### Task 5: Add API endpoints

**Files:**

- Modify: `src/kanban_agent_orchestrator/app.py`

**Steps:**

1. Add `RunQuestionCreate`.
2. Add `QuestionAnswerCreate`.
3. Add `POST /runner/v1/runs/{run_id}/questions`.
4. Add `POST /api/v1/tasks/{task_id}/questions/{question_id}/answer`.
5. Ensure domain errors map through existing `domain_error()`.

### Task 6: Add API tests

**Files:**

- Modify: `tests/test_product.py`

**Tests:**

- `test_runner_question_blocks_task_and_answer_unblocks_it`
- `test_answering_non_task_question_is_rejected`

**Command:**

```bash
uv run pytest tests/test_product.py -q
```

Expected: product/API tests pass.

### Task 7: Create React frontend scaffold

**Files:**

- Create: `web/package.json`
- Create: `web/package-lock.json`
- Create: `web/index.html`
- Create: `web/tsconfig.json`
- Create: `web/tsconfig.node.json`
- Create: `web/vite.config.ts`
- Create: `web/eslint.config.js`
- Create: `web/.prettierrc.json`
- Create: `web/src/main.tsx`
- Create: `web/src/App.tsx`
- Create: `web/src/api.ts`
- Create: `web/src/types.ts`
- Create: `web/src/styles.css`

**Commands:**

```bash
cd /home/nvidia/src/kanban-agent-orchestrator/web
npm install
```

Then implement the minimal UI described above.

### Task 8: Serve built frontend from FastAPI

**Files:**

- Modify: `src/kanban_agent_orchestrator/app.py`

**Steps:**

1. Add `StaticFiles` / `FileResponse` imports.
2. Add `STATIC_DIR`.
3. Mount `/assets` when built assets exist.
4. Change `/` route to serve `static/index.html` with fallback HTML.
5. Remove the large `BOARD_HTML` string once React UI is wired.

### Task 9: Wire frontend build into Docker

**Files:**

- Modify: `Dockerfile`
- Possibly modify: `.dockerignore`

**Steps:**

1. Add Node build stage.
2. Build `web/dist`.
3. Copy dist into `src/kanban_agent_orchestrator/static` before package install or runtime copy.
4. Ensure `.dockerignore` does not exclude required frontend files.

### Task 10: Update CI

**Files:**

- Modify: `.github/workflows/ci.yml`

**Steps:**

1. Set up Node 22.
2. Run frontend install/check/build.
3. Keep existing backend checks.

### Task 11: Update README

**Files:**

- Modify: `README.md`

**Steps:**

1. Document question/block/answer API flow.
2. Document UI usage.
3. Document frontend dev/build commands.
4. Keep examples short.

### Task 12: Run local validation

**Commands:**

```bash
cd /home/nvidia/src/kanban-agent-orchestrator
uv run black .
uv run ruff check .
uv run pytest
npm run format:check --prefix web
npm run lint --prefix web
npm run typecheck --prefix web
npm run build --prefix web
```

Expected:

- Black passes.
- Ruff passes.
- Pytest passes.
- Prettier check passes.
- ESLint passes.
- Typecheck passes.
- Vite build passes.

### Task 13: Live Docker verification

**Commands:**

```bash
docker compose up --build
```

In another shell:

```bash
curl -fsS http://127.0.0.1:8080/healthz
curl -fsS http://127.0.0.1:8080/
```

Smoke test API flow against live container:

1. Create endpoint.
2. Create task.
3. Lease task.
4. Ask question through `/runner/v1/runs/{run_id}/questions`.
5. Confirm task detail shows blocked task and open question.
6. Answer question through `/api/v1/tasks/{task_id}/questions/{question_id}/answer`.
7. Confirm task is ready/todo based on dependencies and question is answered.

Also verify UI visually in browser:

- Board loads.
- Create endpoint works.
- Create task works.
- Selecting a task opens detail panel.
- Blocked task shows open question.
- Answer form resolves/unblocks.
- It does not look like a template generator drank a gallon of Tailwind and blacked out.

Stop stack:

```bash
docker compose down
```

### Task 14: Commit and push directly to main

**Commands:**

```bash
git status --short
git add README.md Dockerfile .dockerignore .github/workflows/ci.yml pyproject.toml src tests web
git commit -m "feat: add task questions and React board UI"
git push origin main
```

If HTTPS push fails in non-interactive shell, use the known working gh credential helper:

```bash
git -c credential.helper='!GH_CONFIG_DIR=/home/nvidia/.config/gh gh auth git-credential' push origin main
```

## Files Likely to Change

- `src/kanban_agent_orchestrator/models.py`
- `src/kanban_agent_orchestrator/kernel.py`
- `src/kanban_agent_orchestrator/app.py`
- `tests/test_kernel.py`
- `tests/test_product.py`
- `README.md`
- `Dockerfile`
- `.dockerignore`
- `.github/workflows/ci.yml`
- `pyproject.toml` only if static package-data config is required
- New `web/` frontend files
- New `src/kanban_agent_orchestrator/static/` only if committing built assets is required; prefer Docker/CI build output over committed generated files unless package serving requires otherwise.

## Validation Checklist

- [ ] Agent can block a task by asking a question from an active run.
- [ ] Question appears in task detail.
- [ ] Question appears in task chat/comments.
- [ ] Answering question records answer and answer comment.
- [ ] Answering resolving question unblocks task.
- [ ] Dependency-gated task unblocks to `todo`, not `ready`.
- [ ] Double-answering a question is rejected.
- [ ] Existing task create/lease/complete/fail/block flows still work.
- [ ] React UI loads from FastAPI root.
- [ ] UI supports board, task detail, comments/questions, and answer action.
- [ ] Python checks pass.
- [ ] Frontend checks pass.
- [ ] Docker image builds.
- [ ] Live container smoke test passes.
- [ ] Changes are pushed directly to `main`.

## Risks and Tradeoffs

- **Adding React introduces Node tooling.** Worth it for a real UI, but keep dependencies boring and minimal.
- **Static asset packaging with `uv_build` may need adjustment.** Verify in Docker; do not assume local source serving means packaged runtime works.
- **Blocked run remains terminal after answer.** That is fine: answering unblocks the task, and a new run can lease it with the answer in task detail. Do not resurrect old runs; zombie runs are how dashboards become haunted.
- **Question/comment duplication is intentional.** Structured question state powers automation; comments preserve readable task chat history.
- **No auth yet.** Current app appears local/internal. Do not invent auth in this task unless Ryan asks.

## Open Questions

- Should answers always unblock resolving questions by default? Plan assumes yes, with `unblock_if_resolved=true` escape hatch.
- Should non-human agents be allowed to answer questions? Plan allows any `answered_by` string; UI defaults to `human`/operator.
- Should multiple open questions all need answers before unblocking? Plan assumes answering a resolving question can unblock immediately. If stricter behavior is wanted, change rule to “unblock only when all `resolves_block` questions for task are answered.”
