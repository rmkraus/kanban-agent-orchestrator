FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    KANBAN_ORCHESTRATOR_DB=/data/orchestrator.json

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev

EXPOSE 8080
VOLUME ["/data"]

CMD ["/app/.venv/bin/kanban-agent-orchestrator", "--host", "0.0.0.0", "--port", "8080"]
