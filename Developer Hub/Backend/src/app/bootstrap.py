"""Process bootstrap: load .env once, before any application module imports.

Import this module FIRST (before any other project import) in every entrypoint:
    main.py, tests/conftest.py, and any standalone script in tools/.

`find_dotenv(usecwd=True)` walks up from CWD to locate Developer Hub/.env, so this
works in Docker, native runs, pytest, and uvicorn reload subprocesses without
hard-coded path math. Existing environment variables always win (override=False),
so docker-compose `env_file:` and explicit shell exports take precedence.
"""
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True), override=False)
