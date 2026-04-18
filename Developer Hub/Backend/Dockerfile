# syntax=docker/dockerfile:1.7
# ─────────────────────────────────────────────────────────────────────
# Backend (Python / FastAPI) — managed with uv on Python 3.13
# ─────────────────────────────────────────────────────────────────────

# Grab the uv binary from its official image (small, fast, no pip bootstrap).
FROM ghcr.io/astral-sh/uv:0.9 AS uv

# ── Builder: resolve & install deps into /app/.venv ──────────────────
FROM python:3.13-slim AS builder

COPY --from=uv /uv /uvx /usr/local/bin/

WORKDIR /app

# uv env tweaks for container builds:
#   • copy instead of hardlink (bind mounts across fs boundaries)
#   • no system Python; always use the pinned interpreter
#   • no uv cache inside the image (we'll mount one)
ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# Install production deps only, using a BuildKit cache for uv.
# Project sources are copied in the next stage so code changes don't
# invalidate the dependency layer.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev


# ── Production: slim runtime with the venv from the builder ──────────
FROM python:3.13-slim AS production

WORKDIR /app

# Copy the resolved venv.
COPY --from=builder /app/.venv /app/.venv

# Copy application code.
COPY src/ ./src/

# Create a non-root user and hand over /app.
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

ENV PYTHON_ENVIRONMENT=Production \
    PYTHONPATH=/app \
    PATH="/app/.venv/bin:$PATH"

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health').read()" || exit 1

EXPOSE 5000

CMD ["python3", "src/main.py"]