# syntax=docker/dockerfile:1

# ---- build ------------------------------------------------------------------
# Dependencies are installed in a throwaway stage so that build tooling and the
# package index cache never reach the runtime image.
FROM python:3.12-slim AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN pip install --no-cache-dir uv

# Copied before the source so that a code change does not invalidate the
# dependency layer.
COPY pyproject.toml README.md ./
COPY src ./src

RUN uv venv /opt/venv \
    && VIRTUAL_ENV=/opt/venv uv pip install --no-cache .

# ---- runtime ----------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# A non-root user: a container process that cannot write its own image is one
# less step available to anyone who finds a way in.
RUN useradd --create-home --uid 10001 app

WORKDIR /app

COPY --from=build /opt/venv /opt/venv
COPY alembic.ini ./
COPY migrations ./migrations

USER app

EXPOSE 8000

# Migrations are deliberately NOT run here. Several replicas start at once, and
# schema changes belong in a single gated step in the deployment pipeline, not
# in a race between containers.
CMD ["m4d", "serve"]
