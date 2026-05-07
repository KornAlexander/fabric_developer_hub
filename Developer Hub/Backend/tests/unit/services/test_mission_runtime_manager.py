from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.mcp import mission_runtime_manager as mrm


def test_runtime_readonly_mounts_only_include_allowed_backend_mounts(monkeypatch) -> None:
    monkeypatch.setenv("HOSTNAME", "backend-container")

    backend_container = SimpleNamespace(
        attrs={
            "Mounts": [
                {"Source": "/host/backend/src", "Destination": "/app/src"},
                {"Source": "/host/repo/mcp", "Destination": "/opt/agenthub-mcp/mcp"},
                {"Source": "/host/repo/external", "Destination": "/opt/agenthub-mcp/external"},
                {"Source": "/host/backend/.data", "Destination": "/app/data"},
                {"Source": "/host/devhub/.env", "Destination": "/app/.env"},
                {"Source": "/var/run/docker.sock", "Destination": "/var/run/docker.sock"},
            ]
        }
    )

    class _Containers:
        def get(self, container_id: str):
            assert container_id == "backend-container"
            return backend_container

    mounts = mrm._runtime_readonly_mounts(SimpleNamespace(containers=_Containers()))

    assert mounts == {
        "/host/backend/src": {"bind": "/app/src", "mode": "ro"},
        "/host/backend/.data": {"bind": "/app/data", "mode": "ro"},
        "/host/repo/mcp": {"bind": "/opt/agenthub-mcp/mcp", "mode": "ro"},
        "/host/repo/external": {"bind": "/opt/agenthub-mcp/external", "mode": "ro"},
    }


def test_runtime_readonly_mounts_derives_native_repo_paths(monkeypatch) -> None:
    monkeypatch.delenv("HOSTNAME", raising=False)
    for env_name in mrm._RUNTIME_ENV_MOUNTS:
        monkeypatch.delenv(env_name, raising=False)

    class _Containers:
        def get(self, _container_id: str):
            raise AssertionError("native backend should not inspect a container")

    mounts = mrm._runtime_readonly_mounts(SimpleNamespace(containers=_Containers()))

    assert mounts
    assert any(bind == {"bind": "/app/src", "mode": "ro"} for bind in mounts.values())
    assert any(bind == {"bind": "/opt/agenthub-mcp/mcp", "mode": "ro"} for bind in mounts.values())
    assert any(bind == {"bind": "/opt/agenthub-mcp/external", "mode": "ro"} for bind in mounts.values())


def test_runtime_environment_does_not_inherit_backend_paths_or_secrets(monkeypatch) -> None:
    monkeypatch.setenv("AGENTHUB_DB_PATH", "/app/data/agenthub.db")
    monkeypatch.setenv("AGENTHUB_EVENT_LEDGER_FILE", "/app/data/events.jsonl")
    monkeypatch.setenv("FABRIC_API_TOKEN", "secret")
    monkeypatch.setenv("MCP_REPO_DIR", "/opt/agenthub-mcp")

    env = mrm._runtime_environment("session-1", 8765)

    assert "AGENTHUB_DB_PATH" not in env
    assert "AGENTHUB_EVENT_LEDGER_FILE" not in env
    assert "FABRIC_API_TOKEN" not in env
    assert env["AGENTHUB_MCP_RUNTIME_SESSION_ID"] == "session-1"
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"


@pytest.mark.asyncio
async def test_cleanup_mission_mcp_runtimes_removes_only_inactive_sessions() -> None:
    removed: list[str] = []

    class _Container:
        def __init__(self, name: str, session_id: str | None) -> None:
            self.name = name
            self.id = name
            self.short_id = name[:12]
            self.labels = {
                "agenthub.kind": "mission-mcp-runtime",
                "agenthub.session_id": session_id,
            }

        def remove(self, *, force: bool) -> None:
            assert force is True
            removed.append(self.name)

    class _Containers:
        def list(self, *, all: bool, filters: dict[str, str]):
            assert all is True
            assert filters == {"label": "agenthub.kind=mission-mcp-runtime"}
            return [
                _Container("active-runtime", "active-session"),
                _Container("orphan-runtime", "orphan-session"),
            ]

    result = await mrm.cleanup_mission_mcp_runtimes(
        active_session_ids={"active-session"},
        docker_client=SimpleNamespace(containers=_Containers()),
    )

    assert result == {"removed": 1, "skipped": 1, "errors": []}
    assert removed == ["orphan-runtime"]