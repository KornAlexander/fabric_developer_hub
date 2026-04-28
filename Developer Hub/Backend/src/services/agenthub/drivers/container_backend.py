"""Container backend protocol and Docker implementation.

The ``ContainerBackend`` protocol abstracts container lifecycle operations
so the ``ContainerSlotRunner`` works identically against Docker (dev) or
Kubernetes (production). Only the backend implementation changes.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SlotContainerConfig:
    """Everything the agent container needs to start."""

    slot_id: str
    agent_id: str
    session_id: str
    assignment_session_id: str
    role: str
    goal: str
    system_prompt: str
    model: str
    max_rounds: int
    allowed_tools: list[str]
    orchestrator_endpoint: str
    copilot_token: str
    workspace_id: str
    budget_remaining_turns: int
    budget_remaining_tool_calls: int
    # Composition-level metadata for logging
    architecture: str = ""
    job_id: str = ""

    def to_env_json(self) -> str:
        """Serialize to base64-encoded JSON for the SLOT_CONFIG env var."""
        d = {
            "slot_id": self.slot_id,
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "assignment_session_id": self.assignment_session_id,
            "role": self.role,
            "goal": self.goal,
            "system_prompt": self.system_prompt,
            "model": self.model,
            "max_rounds": self.max_rounds,
            "allowed_tools": self.allowed_tools,
            "orchestrator_endpoint": self.orchestrator_endpoint,
            "copilot_token": self.copilot_token,
            "workspace_id": self.workspace_id,
            "budget_remaining_turns": self.budget_remaining_turns,
            "budget_remaining_tool_calls": self.budget_remaining_tool_calls,
            "architecture": self.architecture,
            "job_id": self.job_id,
        }
        return base64.b64encode(json.dumps(d).encode()).decode()

    @classmethod
    def from_env_json(cls, encoded: str) -> SlotContainerConfig:
        """Deserialize from the SLOT_CONFIG env var."""
        d = json.loads(base64.b64decode(encoded))
        return cls(**d)


@dataclass(frozen=True)
class ContainerInfo:
    """Metadata about a created container."""

    container_id: str
    name: str
    labels: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class ContainerBackend(Protocol):
    """Abstract container lifecycle operations."""

    async def create(
        self,
        config: SlotContainerConfig,
        *,
        image: str,
        cpu_limit: float,
        memory_limit: str,
        network: str | None,
        labels: dict[str, str] | None,
    ) -> ContainerInfo: ...

    async def start(self, container_id: str) -> None: ...

    async def wait(self, container_id: str, timeout: float) -> int:
        """Wait for the container to exit. Returns the exit code.
        Raises ``asyncio.TimeoutError`` if ``timeout`` is exceeded."""
        ...

    async def kill(self, container_id: str) -> None: ...

    async def remove(self, container_id: str) -> None: ...

    async def logs(self, container_id: str, tail: int = 200) -> str: ...

    async def is_available(self) -> bool:
        """Return True if the backend is functional."""
        ...


class DockerBackend:
    """Container backend implemented via the Docker Engine API.

    Uses the ``docker`` Python SDK (``pip install docker``) to manage
    containers. Wraps synchronous SDK calls in ``asyncio.to_thread``
    so they don't block the event loop.
    """

    def __init__(self) -> None:
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import docker
                self._client = docker.from_env()
            except Exception as exc:
                logger.error("[DOCKER] Failed to connect to Docker: %s", exc)
                raise
        return self._client

    async def is_available(self) -> bool:
        try:
            client = await asyncio.to_thread(self._get_client)
            await asyncio.to_thread(client.ping)
            return True
        except Exception:
            return False

    async def create(
        self,
        config: SlotContainerConfig,
        *,
        image: str,
        cpu_limit: float = 1.0,
        memory_limit: str = "2g",
        network: str | None = None,
        labels: dict[str, str] | None = None,
    ) -> ContainerInfo:
        client = self._get_client()
        name = f"agenthub-agent-{config.session_id[:8]}-{config.slot_id}"
        # Sanitize container name (Docker allows [a-zA-Z0-9_.-])
        name = "".join(c if c.isalnum() or c in "_.-" else "-" for c in name)

        env = {"SLOT_CONFIG": config.to_env_json()}
        container_labels = {
            "agenthub.session_id": config.session_id,
            "agenthub.slot_id": config.slot_id,
            "agenthub.agent_id": config.agent_id,
            "agenthub.job_id": config.job_id,
        }
        if labels:
            container_labels.update(labels)

        # Parse memory limit to bytes for Docker API
        mem_bytes = _parse_memory_limit(memory_limit)

        container = await asyncio.to_thread(
            client.containers.create,
            image=image,
            name=name,
            environment=env,
            labels=container_labels,
            nano_cpus=int(cpu_limit * 1e9),
            mem_limit=mem_bytes,
            memswap_limit=mem_bytes,  # no swap
            pids_limit=100,
            network=network,
            detach=True,
            auto_remove=False,
        )
        logger.info(
            "[DOCKER] Created container %s (%s) for slot %s",
            container.short_id, name, config.slot_id,
        )
        return ContainerInfo(
            container_id=container.id,
            name=name,
            labels=container_labels,
        )

    async def start(self, container_id: str) -> None:
        client = self._get_client()
        container = client.containers.get(container_id)
        await asyncio.to_thread(container.start)
        logger.info("[DOCKER] Started container %s", container_id[:12])

    async def wait(self, container_id: str, timeout: float) -> int:
        client = self._get_client()
        container = client.containers.get(container_id)

        async def _wait():
            result = await asyncio.to_thread(container.wait)
            return result.get("StatusCode", -1)

        return await asyncio.wait_for(_wait(), timeout=timeout)

    async def kill(self, container_id: str) -> None:
        try:
            client = self._get_client()
            container = client.containers.get(container_id)
            await asyncio.to_thread(container.kill)
            logger.info("[DOCKER] Killed container %s", container_id[:12])
        except Exception as exc:
            logger.warning("[DOCKER] Kill failed for %s: %s", container_id[:12], exc)

    async def remove(self, container_id: str) -> None:
        try:
            client = self._get_client()
            container = client.containers.get(container_id)
            await asyncio.to_thread(container.remove, force=True)
            logger.info("[DOCKER] Removed container %s", container_id[:12])
        except Exception as exc:
            logger.warning("[DOCKER] Remove failed for %s: %s", container_id[:12], exc)

    async def logs(self, container_id: str, tail: int = 200) -> str:
        try:
            client = self._get_client()
            container = client.containers.get(container_id)
            raw = await asyncio.to_thread(container.logs, tail=tail)
            return raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        except Exception as exc:
            return f"(failed to read logs: {exc})"


def _parse_memory_limit(limit: str) -> int:
    """Parse a human-readable memory limit like '2g' into bytes."""
    limit = limit.strip().lower()
    multipliers = {"k": 1024, "m": 1024**2, "g": 1024**3}
    if limit[-1] in multipliers:
        return int(float(limit[:-1]) * multipliers[limit[-1]])
    return int(limit)
