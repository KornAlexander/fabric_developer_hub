"""Tests for the container isolation infrastructure.

Tests the ContainerBackend, ContainerPool, ContainerSlotRunner, and
SlotContainerConfig without requiring Docker.
"""

from __future__ import annotations

import asyncio
import base64
import gzip
import json
import time
import sys
from types import SimpleNamespace
import pytest

from domain.models.agent_models import AgentAssignment, Job, JobStatus
from domain.models.dynamic_orchestration import MissionBrief, MissionState, SubagentRun, TaskNode
from services.agenthub.drivers.container_backend import (
    SlotContainerConfig,
    ContainerInfo,
    DockerBackend,
    _parse_memory_limit,
)
from services.agenthub.drivers.container_pool import ContainerPool


# ── SlotContainerConfig ──────────────────────────────────────────

class TestSlotContainerConfig:
    def _sample_config(self) -> SlotContainerConfig:
        return SlotContainerConfig(
            slot_id="slot-1",
            agent_id="fabric-data-engineer",
            session_id="sess-abc123",
            assignment_session_id="assign-xyz",
            role="Data Engineer",
            goal="Build the lakehouse",
            system_prompt="You are a data engineer.",
            model="gpt-4o",
            max_rounds=10,
            allowed_tools=["fabric_list_items", "fabric_read_file"],
            orchestrator_endpoint="http://host.docker.internal:5000",
            copilot_token="test-token",
            workspace_id="ws-123",
            budget_remaining_turns=15,
            budget_remaining_tool_calls=80,
            architecture="supervisor",
            job_id="job-1",
        )

    def test_round_trip_serialization(self):
        """Config serializes to base64 JSON and deserializes back."""
        config = self._sample_config()
        encoded = config.to_env_json()

        # Must be valid base64
        raw = base64.b64decode(encoded)
        data = json.loads(raw)
        assert data["slot_id"] == "slot-1"
        assert data["agent_id"] == "fabric-data-engineer"
        assert data["allowed_tools"] == ["fabric_list_items", "fabric_read_file"]

        # Round-trip back to object
        restored = SlotContainerConfig.from_env_json(encoded)
        assert restored.slot_id == config.slot_id
        assert restored.agent_id == config.agent_id
        assert restored.goal == config.goal
        assert restored.allowed_tools == config.allowed_tools

    def test_serialization_does_not_leak_unexpected_fields(self):
        """The serialized JSON contains only expected fields."""
        config = self._sample_config()
        raw = json.loads(base64.b64decode(config.to_env_json()))
        expected_keys = {
            "slot_id", "agent_id", "session_id", "assignment_session_id",
            "role", "goal", "system_prompt", "model", "max_rounds",
            "allowed_tools", "orchestrator_endpoint", "copilot_token",
            "workspace_id", "budget_remaining_turns",
            "budget_remaining_tool_calls", "architecture", "job_id",
        }
        assert set(raw.keys()) == expected_keys

    def test_large_config_is_compressed_for_container_env_limit(self):
        """Large verifier prompts are compressed before going into SLOT_CONFIG."""
        config = self._sample_config()
        config = SlotContainerConfig(
            **{
                **config.__dict__,
                "goal": "verify this report\n" * 6000,
            }
        )

        encoded = config.to_env_json()
        raw = base64.b64decode(encoded)

        assert raw.startswith(b"\x1f\x8b")
        assert b"verify this report" in gzip.decompress(raw)
        assert len(encoded) < len(json.dumps(config.__dict__).encode())
        assert SlotContainerConfig.from_env_json(encoded).goal == config.goal


# ── Memory limit parser ──────────────────────────────────────────

class TestParseMemoryLimit:
    def test_gigabytes(self):
        assert _parse_memory_limit("2g") == 2 * 1024**3

    def test_megabytes(self):
        assert _parse_memory_limit("512m") == 512 * 1024**2

    def test_kilobytes(self):
        assert _parse_memory_limit("1024k") == 1024 * 1024

    def test_raw_bytes(self):
        assert _parse_memory_limit("1073741824") == 1073741824


# ── ContainerPool ────────────────────────────────────────────────

class TestContainerPool:
    @pytest.mark.asyncio
    async def test_acquire_release_cycle(self):
        pool = ContainerPool(max_concurrent=2)
        assert pool.active_count == 0

        await pool.acquire()
        assert pool.active_count == 1

        await pool.acquire()
        assert pool.active_count == 2

        pool.release()
        assert pool.active_count == 1

        pool.release()
        assert pool.active_count == 0

    @pytest.mark.asyncio
    async def test_context_manager(self):
        pool = ContainerPool(max_concurrent=3)
        async with pool:
            assert pool.active_count == 1
        assert pool.active_count == 0

    @pytest.mark.asyncio
    async def test_backpressure_blocks(self):
        """When pool is full, acquire blocks until a slot is released."""
        pool = ContainerPool(max_concurrent=1)
        await pool.acquire()

        acquired = False

        async def try_acquire():
            nonlocal acquired
            await pool.acquire()
            acquired = True

        task = asyncio.create_task(try_acquire())
        await asyncio.sleep(0.05)
        assert not acquired  # blocked

        pool.release()
        await asyncio.sleep(0.05)
        assert acquired  # unblocked

        pool.release()
        task.cancel()


# ── MockBackend for ContainerSlotRunner tests ────────────────────

class MockContainerBackend:
    """In-memory mock that simulates container lifecycle."""

    def __init__(self, exit_code: int = 0, fail_create: bool = False):
        self._exit_code = exit_code
        self._fail_create = fail_create
        self.created: list[dict] = []
        self.started: list[str] = []
        self.killed: list[str] = []
        self.removed: list[str] = []

    async def is_available(self) -> bool:
        return not self._fail_create

    async def create(self, config, *, image, cpu_limit=1.0, memory_limit="2g", network=None, labels=None):
        if self._fail_create:
            raise RuntimeError("Docker unavailable")
        cid = f"mock-{config.slot_id}"
        self.created.append({"id": cid, "config": config})
        return ContainerInfo(container_id=cid, name=f"test-{config.slot_id}")

    async def start(self, container_id):
        self.started.append(container_id)

    async def wait(self, container_id, timeout):
        await asyncio.sleep(0.01)  # simulate brief execution
        return self._exit_code

    async def kill(self, container_id):
        self.killed.append(container_id)

    async def remove(self, container_id):
        self.removed.append(container_id)

    async def logs(self, container_id, tail=200):
        return f"[mock logs for {container_id}]"


class TestMockBackendSmokeTest:
    """Verify the mock backend implements the expected protocol."""

    @pytest.mark.asyncio
    async def test_lifecycle(self):
        backend = MockContainerBackend(exit_code=0)
        config = SlotContainerConfig(
            slot_id="s1", agent_id="test", session_id="sess",
            assignment_session_id="assgn", role="test", goal="test",
            system_prompt="test", model="gpt-4o", max_rounds=5,
            allowed_tools=[], orchestrator_endpoint="http://localhost:5000",
            copilot_token="tok", workspace_id="ws",
            budget_remaining_turns=10, budget_remaining_tool_calls=50,
        )
        info = await backend.create(config, image="test:latest")
        await backend.start(info.container_id)
        code = await backend.wait(info.container_id, timeout=30)
        await backend.remove(info.container_id)

        assert code == 0
        assert len(backend.created) == 1
        assert len(backend.started) == 1
        assert len(backend.removed) == 1


# ── Docker warm pool ─────────────────────────────────────────────


def _reset_docker_warm_pool() -> None:
    DockerBackend._warm_ready.clear()
    DockerBackend._warm_inflight.clear()
    for task in DockerBackend._warm_refill_tasks.values():
        task.cancel()
    DockerBackend._warm_refill_tasks.clear()


@pytest.mark.asyncio
async def test_docker_backend_prewarms_idle_hardened_agent_containers(monkeypatch):
    _reset_docker_warm_pool()
    monkeypatch.setenv("AGENT_CONTAINER_WARM_POOL_SIZE", "2")

    class _FakeContainer:
        def __init__(self, container_id: str):
            self.id = container_id
            self.short_id = container_id[:12]
            self.started = False
            self.removed = False

        def start(self):
            self.started = True

        def remove(self, force=True):
            self.removed = True

    class _FakeContainers:
        def __init__(self):
            self.created: list[dict] = []
            self.by_id: dict[str, _FakeContainer] = {}

        def create(self, **kwargs):
            container = _FakeContainer(f"warm-{len(self.created)}")
            self.created.append(kwargs)
            self.by_id[container.id] = container
            return container

        def get(self, container_id: str):
            return self.by_id[container_id]

    containers = _FakeContainers()
    fake_client = SimpleNamespace(containers=containers)
    monkeypatch.setitem(sys.modules, "docker", SimpleNamespace(from_env=lambda: fake_client))

    result = await DockerBackend().prewarm(
        image="agent:latest",
        cpu_limit=1.0,
        memory_limit="512m",
        network="agent-net",
    )

    assert result["target"] == 2
    assert result["ready"] == 2
    assert result["created"] == 2
    assert len(containers.created) == 2
    first = containers.created[0]
    assert first["entrypoint"][:2] == ["python", "-c"]
    assert "SLOT_CONFIG" not in first["environment"]
    assert first["environment"]["AGENTHUB_WARM_CONTAINER"] == "1"
    assert first["labels"]["agenthub.warm"] == "true"
    assert first["read_only"] is True
    assert first["cap_drop"] == ["ALL"]
    assert all(container.started for container in containers.by_id.values())

    cleanup = await DockerBackend().cleanup_warm_pool()
    assert cleanup["removed"] == 2
    assert all(container.removed for container in containers.by_id.values())


@pytest.mark.asyncio
async def test_docker_backend_runs_agent_inside_warm_container_with_slot_config(monkeypatch):
    _reset_docker_warm_pool()
    monkeypatch.setenv("AGENT_CONTAINER_WARM_POOL_SIZE", "1")

    exec_calls: list[dict] = []

    class _FakeContainer:
        id = "warm-agent-1"
        short_id = "warm-agent-1"

        def start(self):
            pass

        def exec_run(self, command, **kwargs):
            exec_calls.append({"command": command, **kwargs})
            return SimpleNamespace(exit_code=0, output=b"agent completed")

        def remove(self, force=True):
            pass

    class _FakeContainers:
        def __init__(self):
            self.container = _FakeContainer()

        def create(self, **kwargs):
            return self.container

        def get(self, container_id: str):
            assert container_id == "warm-agent-1"
            return self.container

    fake_client = SimpleNamespace(containers=_FakeContainers())
    monkeypatch.setitem(sys.modules, "docker", SimpleNamespace(from_env=lambda: fake_client))

    config = SlotContainerConfig(
        slot_id="slot-1",
        agent_id="fabric-data-engineer",
        session_id="sess-abc123",
        assignment_session_id="assign-xyz",
        role="Data Engineer",
        goal="Build the lakehouse",
        system_prompt="You are a data engineer.",
        model="gpt-4o",
        max_rounds=10,
        allowed_tools=["fabric_list_items"],
        orchestrator_endpoint="http://backend:5000",
        copilot_token="test-token",
        workspace_id="ws-123",
        budget_remaining_turns=15,
        budget_remaining_tool_calls=80,
    )

    backend = DockerBackend()
    await backend.prewarm(
        image="agent:latest",
        cpu_limit=1.0,
        memory_limit="512m",
        network="agent-net",
    )
    warm = await backend.acquire_warm_container(
        config,
        image="agent:latest",
        cpu_limit=1.0,
        memory_limit="512m",
        network="agent-net",
    )
    assert warm is not None

    result = await backend.run_agent_in_warm_container(warm.container_id, config, timeout=5)

    assert result.exit_code == 0
    assert result.logs == "agent completed"
    assert exec_calls[0]["command"] == ["python", "-m", "agent"]
    assert exec_calls[0]["workdir"] == "/app"
    assert exec_calls[0]["user"] == "agentuser"
    restored = SlotContainerConfig.from_env_json(exec_calls[0]["environment"]["SLOT_CONFIG"])
    assert restored.slot_id == "slot-1"

    await backend.cleanup_warm_pool()


# ── ContainerSlotRunner signature compatibility ──────────────────


def test_container_slot_runner_run_slot_accepts_step_label():
    """Regression: ``ContainerAgent`` calls ``runner.run_slot(...,
    step_label=...)`` for both the in-process and container runner.
    Both must accept the kwarg or the container path crashes with
    ``TypeError: run_slot() got an unexpected keyword argument
    'step_label'`` the first time MAF invokes it."""
    import inspect

    from services.agenthub.drivers.container_runner import ContainerSlotRunner
    from services.agenthub.drivers.slot_runner import SlotRunner

    container_sig = inspect.signature(ContainerSlotRunner.run_slot)
    inproc_sig = inspect.signature(SlotRunner.run_slot)

    # Same kwargs the MAF ContainerAgent currently uses must be
    # accepted by *both* runners.
    for name in ("upstream_handoffs", "max_turns", "step_label", "allowed_tools_override"):
        assert name in container_sig.parameters, (
            f"ContainerSlotRunner.run_slot must accept '{name}' kwarg"
        )
        assert name in inproc_sig.parameters, (
            f"SlotRunner.run_slot must accept '{name}' kwarg"
        )


def test_container_runner_pi_terminal_events_use_dynamic_run_context(monkeypatch):
    from services.agenthub.drivers.container_runner import _emit_pi_subagent_runner_terminal

    monkeypatch.setenv("AGENTHUB_PI_OBSERVABILITY", "pi-subagents")
    job = Job(
        id="session-1",
        user_id="user-1",
        workspace_id="ws-1",
        task_description="do work",
        status=JobStatus.RUNNING,
    )
    mission = MissionState(
        brief=MissionBrief(session_id=job.id, goal="do work", workspace_id="ws-1"),
    )
    mission.tasks["task-1"] = TaskNode(id="task-1", title="Container task", objective="observe")
    mission.subagent_runs["run-1"] = SubagentRun(
        id="run-1",
        task_id="task-1",
        agent_id="fabric-data-engineer",
        agent_session_id="assignment-1",
    )
    events: list[dict] = []

    class _Execution:
        dynamic_mission_state = mission

        def __init__(self):
            self.job = job

        def emit(self, event_type, **kwargs):
            event = {"type": event_type, **kwargs}
            events.append(event)
            return event

    assignment = AgentAssignment(
        agent_id="fabric-data-engineer",
        session_id="assignment-1",
        role="Engineer",
        goal="observe",
    )
    _emit_pi_subagent_runner_terminal(
        _Execution(),
        assignment,
        agent_name="Fabric Data Engineer",
        control_type="needs_attention",
        to="failed",
        message="The isolated Pi subagent container timed out.",
        reason="timeout",
        state="failed",
        result_status="failed",
        summary="Timed out while running the isolated Pi subagent container.",
        started_at=time.monotonic() - 1,
        tool_count=2,
    )

    assert [event["type"] for event in events] == [
        "pi.subagents.control",
        "pi.subagents.result",
        "pi.subagents.status",
    ]
    assert all(event["runId"] == "run-1" for event in events)
    assert all(event["taskId"] == "task-1" for event in events)
    assert events[0]["controlType"] == "needs_attention"
    assert events[1]["status"] == "failed"
    assert events[2]["state"] == "failed"


@pytest.mark.asyncio
async def test_docker_backend_creates_hardened_agent_container(monkeypatch):
    from services.agenthub.drivers.container_backend import DockerBackend

    captured: dict = {}

    class _FakeContainer:
        id = "container-id"
        short_id = "container"

    class _FakeContainers:
        def create(self, **kwargs):
            captured.update(kwargs)
            return _FakeContainer()

    fake_client = SimpleNamespace(containers=_FakeContainers())
    monkeypatch.setitem(sys.modules, "docker", SimpleNamespace(from_env=lambda: fake_client))

    config = SlotContainerConfig(
        slot_id="s1", agent_id="test", session_id="sess",
        assignment_session_id="assgn", role="test", goal="test",
        system_prompt="test", model="gpt-4o", max_rounds=5,
        allowed_tools=[], orchestrator_endpoint="http://localhost:5000",
        copilot_token="tok", workspace_id="ws",
        budget_remaining_turns=10, budget_remaining_tool_calls=50,
    )

    await DockerBackend().create(config, image="agent:latest")

    assert captured["read_only"] is True
    assert captured["cap_drop"] == ["ALL"]
    assert captured["security_opt"] == ["no-new-privileges:true"]
    assert captured["privileged"] is False
    assert captured["extra_hosts"] == {"host.docker.internal": "host-gateway"}
    assert "/tmp" in captured["tmpfs"]
