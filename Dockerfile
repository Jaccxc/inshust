# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm

# uv binary (pinned-ish via tag); copy from the official image
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# 1) Dependency layer (cached unless lock/manifest change)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 2) Project layer
COPY . .
RUN uv sync --frozen --no-dev

# Durable state (instagrapi session blob + SQLite) lives here.
VOLUME ["/data"]

# Long-running worker: realtime MQTT listener + polling safety net.
CMD ["inshust"]
