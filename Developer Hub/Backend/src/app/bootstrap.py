"""Process bootstrap: load .env once, before any application module imports.

Import this module FIRST (before any other project import) in every entrypoint:
    main.py, tests/conftest.py, and any standalone script in tools/.

Search order for .env:
  1. ``find_dotenv(usecwd=True)``  — walks up from CWD.
  2. Walk up from this file's location — covers the case where CWD is unhelpful
     (e.g. when uvicorn reload subprocesses chdir somewhere else).

Existing environment variables always win (override=False), so docker-compose
``env_file:`` and explicit shell exports take precedence over .env contents.

In Docker the .env file is read by docker-compose on the host and the resulting
variables are injected into the container — there is no .env file inside the
container. That is fine: bootstrap only emits a loud warning when no .env is
found AND the expected app variables (CLIENT_ID / PUBLISHER_TENANT_ID) are
ALSO missing from the environment. Otherwise it logs an INFO-level message.
"""
import os
import sys
from pathlib import Path

from dotenv import find_dotenv, load_dotenv


def _walk_up_for_dotenv() -> str:
    """Walk parents of ``__file__`` looking for a ``.env`` file."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / ".env"
        if candidate.is_file():
            return str(candidate)
    return ""


# 1) Standard CWD-based discovery.
DOTENV_PATH: str = find_dotenv(usecwd=True) or _walk_up_for_dotenv()
DOTENV_LOADED: bool = bool(DOTENV_PATH) and load_dotenv(DOTENV_PATH, override=False)

# Marker vars expected to be present after bootstrap. If at least one is set,
# the environment is considered "configured" even when no .env file was found
# (the typical Docker-compose env_file:/shell-export case).
_MARKER_VARS = ("CLIENT_ID", "PUBLISHER_TENANT_ID")
_ENV_LOOKS_CONFIGURED: bool = any(os.environ.get(v) for v in _MARKER_VARS)

# Stderr-print at import time so the operator sees env-loading status even
# before logging is configured. Skipped under pytest to avoid noisy output.
# Also skipped in uvicorn reload-child processes (parent already printed);
# the marker env var inherits from parent -> child on fork/spawn.
_BOOTSTRAP_MARKER = "_AGENTHUB_BOOTSTRAP_PRINTED"
if (
    "pytest" not in sys.modules
    and "PYTEST_CURRENT_TEST" not in os.environ
    and os.environ.get(_BOOTSTRAP_MARKER) != "1"
):
    if DOTENV_LOADED:
        print(f"[bootstrap] loaded .env from {DOTENV_PATH}", file=sys.stderr)
    elif _ENV_LOOKS_CONFIGURED:
        print(
            "[bootstrap] no .env file on disk; using already-populated environment "
            "(docker-compose env_file: or shell exports)",
            file=sys.stderr,
        )
    else:
        print(
            "[bootstrap] WARNING: no .env file found AND expected vars "
            f"({', '.join(_MARKER_VARS)}) are not set — startup will likely fail",
            file=sys.stderr,
        )
    os.environ[_BOOTSTRAP_MARKER] = "1"
