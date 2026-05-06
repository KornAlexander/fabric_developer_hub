# syntax=docker/dockerfile:1.7
# ─────────────────────────────────────────────────────────────────────
# SLL Sidecar — runs Michael Kovalsky's semantic-link-labs against an
# arbitrary Fabric semantic model and returns the *literal* HTML/JSON
# output of `run_model_bpa()` and `vertipaq_analyzer()` to the backend.
#
# The sempy stack pulls in pythonnet + AMO/ADOMD .NET assemblies for
# TOM access, so we layer Python on top of the Microsoft .NET 8 runtime
# image instead of `python:3.11-slim`.
# ─────────────────────────────────────────────────────────────────────

FROM mcr.microsoft.com/dotnet/runtime:8.0-bookworm-slim AS base

# Install Python 3.11 + build deps required by pythonnet wheels.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv \
        gcc g++ libffi-dev \
        ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# Use an isolated venv so we don't fight Debian's PEP 668.
ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip wheel \
    && pip install --no-cache-dir -r requirements.txt

COPY app.py ./

EXPOSE 5100

# Single-worker by design: sempy/pythonnet share a process-wide CLR
# host; concurrent requests would race the same TOM connection cache.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "5100", "--workers", "1"]
