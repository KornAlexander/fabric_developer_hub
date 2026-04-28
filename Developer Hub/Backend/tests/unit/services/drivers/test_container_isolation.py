"""Tests for the container isolation infrastructure.

Tests the ContainerBackend, ContainerPool, ContainerSlotRunner, and
SlotContainerConfig without requiring Docker.
"""

from __future__ import annotations

import asyncio
import base64
import json
import pytest

from services.agenthub.drivers.container_backend import (
    SlotContainerConfig,
    ContainerInfo,
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
    for name in ("upstream_handoffs", "max_turns", "step_label"):
        assert name in container_sig.parameters, (
            f"ContainerSlotRunner.run_slot must accept '{name}' kwarg"
        )
        assert name in inproc_sig.parameters, (
            f"SlotRunner.run_slot must accept '{name}' kwarg"
        )
