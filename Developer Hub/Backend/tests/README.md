# Testing Guide — Developer Hub Backend

All test configuration is in **`pyproject.toml`** — there is no `pytest.ini`,
no `requirements-test.txt`, and no `run_tests.py`. One source of truth.

- **Test deps** → `[dependency-groups].dev` in `pyproject.toml`
- **Pytest config** → `[tool.pytest.ini_options]` in `pyproject.toml`
  (testpaths, pythonpath, asyncio mode, addopts including coverage,
  registered markers, warning filters)

## 📋 Prerequisites

- **Python 3.13+**
- **[uv](https://docs.astral.sh/uv/)** (recommended) or `pip`

## 🚀 Quick Start

```bash
cd "Developer Hub/Backend"

# Install runtime + dev dependencies into .venv
uv sync --group dev

# Run the full suite (uses pyproject addopts: -v, coverage, strict markers)
uv run pytest
```

If you have a venv already activated, you can drop `uv run` and call `pytest` directly.

## 📊 Common Commands

```bash
# Subsets via markers (declared in pyproject)
uv run pytest -m unit
uv run pytest -m integration
uv run pytest -m "unit and not slow"

# A single file or test
uv run pytest tests/unit/services/agenthub/test_session_store.py
uv run pytest tests/unit/services/agenthub/test_session_store.py::test_create_session
uv run pytest -k test_create_session

# Parallel execution (pytest-xdist is in dev deps)
uv run pytest -n auto

# Verbose / debug
uv run pytest -vv -s --tb=long

# Coverage HTML report (terminal coverage already runs by default)
uv run pytest --cov-report=html
# → open htmlcov/index.html
```

## 📁 Test Structure

```
Backend/
├── pyproject.toml          # Single source of truth (deps + pytest config)
├── src/                    # Application source code (added to sys.path via pythonpath)
└── tests/
    ├── conftest.py         # Pytest fixtures and shared setup
    ├── test_fixtures.py    # Common test data
    ├── test_helpers.py     # Helper utilities
    ├── constants/          # Test constants
    ├── unit/               # Unit tests (marker: unit)
    │   ├── api/
    │   ├── controllers/
    │   └── services/
    └── integration/        # Integration tests (marker: integration)
```

## 📈 Coverage

Coverage is enabled by default via `addopts` in `pyproject.toml` with a
minimum threshold (`--cov-fail-under`). Adjust the threshold in
`pyproject.toml` if needed; do not duplicate it elsewhere.

## ✍️ Writing Tests

### Naming
- Files: `test_*.py`
- Classes: `Test*`
- Functions: `test_*`

### Markers (registered in pyproject.toml)
- `unit` — fast, isolated unit tests
- `integration` — multi-component tests
- `api` — API endpoint tests
- `controllers` — controller layer tests
- `services` — service layer tests
- `models` — model / domain entity tests
- `slow` — long-running tests
- `smoke` — critical CI/CD smoke tests

### Async tests
`asyncio_mode = "auto"` is set in `pyproject.toml`, so async test
functions do not need an explicit `@pytest.mark.asyncio` decorator.

### Common fixtures (from `conftest.py`)
- `client` — FastAPI test client
- `valid_headers` — pre-configured request headers
- `mock_authentication_service`, `mock_item_factory`
- `app` — FastAPI app instance

## 🔧 Troubleshooting

**Import errors** — ensure you ran `uv sync --group dev` and that
`pythonpath = ["src"]` is present in `[tool.pytest.ini_options]`.

**Coverage below threshold** — run `uv run pytest --cov-report=term-missing`
to see uncovered lines, or `--cov-report=html` for a clickable report.

**Marker warnings** — add the marker to `[tool.pytest.ini_options].markers`
in `pyproject.toml`; do not silence with inline ignores.

## 🚀 CI/CD

```yaml
- uses: actions/setup-python@v5
  with: { python-version: '3.13' }
- uses: astral-sh/setup-uv@v3
- run: uv sync --group dev
  working-directory: Developer Hub/Backend
- run: uv run pytest
  working-directory: Developer Hub/Backend
```
