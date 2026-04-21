# Production image for the quant runner.
# Deployed to the VPS but also usable locally for reproducible runs.
FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        git \
        libpq-dev \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

# Install uv (pinned to a recent release line).
COPY --from=ghcr.io/astral-sh/uv:0.5.5 /uv /usr/local/bin/uv

WORKDIR /opt/quant

# Lock-first install for caching.
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-install-project --no-dev || uv sync --no-install-project --no-dev

# Copy the rest of the project.
COPY . .
RUN uv sync --frozen --no-dev || uv sync --no-dev

ENV PATH="/opt/quant/.venv/bin:$PATH" \
    PYTHONPATH="/opt/quant/src"

# Default entrypoint runs the live scheduler; override for one-shot tasks.
CMD ["uv", "run", "python", "-m", "quant.live.scheduler"]
