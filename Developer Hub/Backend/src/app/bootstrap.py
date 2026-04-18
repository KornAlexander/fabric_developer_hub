"""Process bootstrap: load .env once, before any application module imports.

Import this module FIRST (before any other project import) in every entrypoint:
    main.py, tests/conftest.py, and any standalone script in tools/.

`find_dotenv(usecwd=True)` walks up from CWD to locate Developer Hub/.env, so this
works in Docker, native runs, pytest, and uvicorn reload subprocesses without
hard-coded path math. Existing environment variables always win (override=False),
so docker-compose `env_file:` and explicit shell exports take precedence.

The resolved path (or the fact that none was found) is recorded on this module
so callers can surface it from a logger that hasn't been configured yet at
import time. ``main.py`` echoes ``DOTENV_PATH`` at INFO during startup.
"""
import os
import sys

from dotenv import find_dotenv, load_dotenv

# find_dotenv returns "" when nothing was found; make that explicit.
DOTENV_PATH: str = find_dotenv(usecwd=True)
DOTENV_LOADED: bool = bool(DOTENV_PATH) and load_dotenv(DOTENV_PATH, override=False)

# Stderr-print at import time so the operator sees env-loading status even
# before logging is configured. Skipped under pytest to avoid noisy output.
if "pytest" not in sys.modules and "PYTEST_CURRENT_TEST" not in os.environ:
    if DOTENV_LOADED:
        print(f"[bootstrap] loaded .env from {DOTENV_PATH}", file=sys.stderr)
    else:
        print(
            "[bootstrap] no .env file found via find_dotenv(usecwd=True); "
            "relying on already-set environment variables",
            file=sys.stderr,
        )
