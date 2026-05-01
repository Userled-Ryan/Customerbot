FROM python:3.14-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-dev --no-group docs --no-install-project

COPY README.md ./
COPY scripts/ scripts/
COPY src/ src/

RUN uv sync --frozen --no-dev --no-group docs

RUN mkdir -p /data

EXPOSE 8080

CMD ["uv", "run", "--no-sync", "uvicorn", "customerbot.main:api", "--host", "0.0.0.0", "--port", "8080"]
