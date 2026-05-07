"""Container backend protocol and Docker implementation.

The ``ContainerBackend`` protocol abstracts container lifecycle operations
so the ``ContainerSlotRunner`` works identically against Docker (dev) or
Kubernetes (production). Only the backend implementation changes.
"""

from __future__ import annotations

import asyncio
import base64
import gzip
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, ClassVar, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

_SLOT_CONFIG_COMPRESSION_THRESHOLD_BYTES = 16 * 1024
_DEFAULT_IMAGE = "developer-hub-agent:dev"
_DEFAULT_CPU = 1.0
_DEFAULT_MEMORY = "2g"
_DEFAULT_WARM_POOL_SIZE = 2
_WARM_IDLE_ENTRYPOINT = [
    "python",
    "-c",
    "import time; time.sleep(31536000)",
]


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
        """Serialize to base64-encoded config for the SLOT_CONFIG env var.

        Docker passes environment variables through the container init argv. Rich
        verifier prompts can make the raw JSON large enough to hit the kernel's
        argv/env limit before Python starts, so large configs are gzip-compressed
        before base64 encoding. Small configs stay plain JSON for readability and
        backwards compatibility with existing tests/tools.
        """
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
        raw = json.dumps(d).encode()
        if len(raw) > _SLOT_CONFIG_COMPRESSION_THRESHOLD_BYTES:
            raw = gzip.compress(raw)
        return base64.b64encode(raw).decode()

    @classmethod
    def from_env_json(cls, encoded: str) -> SlotContainerConfig:
        """Deserialize from the SLOT_CONFIG env var."""
        raw = base64.b64decode(encoded)
        if raw.startswith(b"\x1f\x8b"):
            raw = gzip.decompress(raw)
        d = json.loads(raw)
        return cls(**d)


@dataclass(frozen=True)
class ContainerInfo:
    """Metadata about a created container."""

    container_id: str
    name: str
    labels: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ContainerRunResult:
    """Result from running the agent process inside a warm container."""

    exit_code: int
    logs: str


def agent_container_options_from_env() -> dict[str, Any]:
    """Return the container runtime options shared by cold and warm paths."""
    return {
        "image": os.environ.get("AGENT_IMAGE", _DEFAULT_IMAGE),
        "cpu_limit": float(os.environ.get("AGENT_CONTAINER_CPUS", str(_DEFAULT_CPU))),
        "memory_limit": os.environ.get("AGENT_CONTAINER_MEMORY", _DEFAULT_MEMORY),
        "network": os.environ.get("AGENT_NETWORK") or None,
    }


def warm_pool_target_from_env() -> int:
    """Configured number of idle agent sandboxes to keep ready."""
    raw = os.environ.get("AGENT_CONTAINER_WARM_POOL_SIZE")
    if raw is None:
        if os.environ.get("AGENT_ISOLATION", "inprocess").lower() == "container":
            return _DEFAULT_WARM_POOL_SIZE
        return 0
    try:
        return max(0, int(raw))
    except ValueError:
        logger.warning("[DOCKER] Invalid AGENT_CONTAINER_WARM_POOL_SIZE=%r; disabling warm pool", raw)
        return 0


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

    _warm_ready: ClassVar[dict[tuple[str, float, str, str | None], list[ContainerInfo]]] = {}
    _warm_inflight: ClassVar[dict[tuple[str, float, str, str | None], int]] = {}
    _warm_refill_tasks: ClassVar[dict[tuple[str, float, str, str | None], asyncio.Task]] = {}
    _warm_lock: ClassVar[asyncio.Lock | None] = None
    _warm_lock_loop: ClassVar[asyncio.AbstractEventLoop | None] = None

    def __init__(self) -> None:
        self._client = None

    @classmethod
    def _get_warm_lock(cls) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if cls._warm_lock is None or cls._warm_lock_loop is not loop:
            cls._warm_lock = asyncio.Lock()
            cls._warm_lock_loop = loop
        return cls._warm_lock

    def _get_client(self):
        if self._client is None:
            try:
                import docker
                self._client = docker.from_env()
            except Exception as exc:
                logger.error("[DOCKER] Failed to connect to Docker: %s", exc)
                raise
        return self._client

    def _current_network(self, client) -> str | None:
        configured = os.environ.get("AGENT_NETWORK")
        if configured:
            return configured
        container_id = os.environ.get("AGENTHUB_CURRENT_CONTAINER_ID") or os.environ.get("HOSTNAME")
        if not container_id:
            return None
        try:
            current = client.containers.get(container_id)
            networks = current.attrs.get("NetworkSettings", {}).get("Networks", {})
            if networks:
                return next(iter(networks.keys()))
        except Exception as exc:
            logger.warning("[DOCKER] Could not inspect current container network: %s", exc)
        return None

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
        name = f"developer-hub-agent-{config.session_id[:8]}-{config.slot_id}"
        env = {
            "SLOT_CONFIG": config.to_env_json(),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
        }
        container_labels = {
            "agenthub.session_id": config.session_id,
            "agenthub.slot_id": config.slot_id,
            "agenthub.agent_id": config.agent_id,
            "agenthub.job_id": config.job_id,
        }
        if labels:
            container_labels.update(labels)
        return await self._create_agent_container(
            image=image,
            name=name,
            environment=env,
            labels=container_labels,
            cpu_limit=cpu_limit,
            memory_limit=memory_limit,
            network=network,
        )

    async def _create_agent_container(
        self,
        *,
        image: str,
        name: str,
        environment: dict[str, str],
        labels: dict[str, str],
        cpu_limit: float,
        memory_limit: str,
        network: str | None,
        entrypoint: list[str] | None = None,
    ) -> ContainerInfo:
        client = self._get_client()
        network = network or self._current_network(client)
        # Sanitize container name (Docker allows [a-zA-Z0-9_.-])
        name = "".join(c if c.isalnum() or c in "_.-" else "-" for c in name)

        # Parse memory limit to bytes for Docker API
        mem_bytes = _parse_memory_limit(memory_limit)

        create_kwargs = dict(
            image=image,
            name=name,
            environment=environment,
            labels=labels,
            nano_cpus=int(cpu_limit * 1e9),
            mem_limit=mem_bytes,
            memswap_limit=mem_bytes,  # no swap
            pids_limit=100,
            network=network,
            read_only=True,
            tmpfs={"/tmp": "rw,nosuid,nodev,noexec,size=64m"},
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            privileged=False,
            init=True,
            detach=True,
            auto_remove=False,
            extra_hosts={"host.docker.internal": "host-gateway"},
        )
        if entrypoint is not None:
            create_kwargs["entrypoint"] = entrypoint

        container = await asyncio.to_thread(
            client.containers.create,
            **create_kwargs,
        )
        logger.info(
            "[DOCKER] Created container %s (%s)",
            container.short_id, name,
        )
        return ContainerInfo(
            container_id=container.id,
            name=name,
            labels=labels,
        )

    async def prewarm_from_env(self) -> dict[str, Any]:
        options = agent_container_options_from_env()
        return await self.prewarm(
            image=options["image"],
            cpu_limit=options["cpu_limit"],
            memory_limit=options["memory_limit"],
            network=options["network"],
        )

    async def prewarm(
        self,
        *,
        image: str,
        cpu_limit: float,
        memory_limit: str,
        network: str | None,
        target_size: int | None = None,
    ) -> dict[str, Any]:
        target = warm_pool_target_from_env() if target_size is None else max(0, target_size)
        profile = (image, cpu_limit, memory_limit, network)
        if target <= 0:
            return {"target": 0, "ready": 0, "created": 0, "errors": []}

        lock = self._get_warm_lock()
        async with lock:
            ready = self._warm_ready.setdefault(profile, [])
            inflight = self._warm_inflight.get(profile, 0)
            to_create = max(0, target - len(ready) - inflight)
            self._warm_inflight[profile] = inflight + to_create

        created: list[ContainerInfo] = []
        errors: list[str] = []
        for _ in range(to_create):
            try:
                container = await self._create_warm_container(
                    image=image,
                    cpu_limit=cpu_limit,
                    memory_limit=memory_limit,
                    network=network,
                )
                await self.start(container.container_id)
                created.append(container)
            except Exception as exc:
                logger.warning("[DOCKER] Failed to prewarm agent container: %s", exc)
                errors.append(str(exc))

        async with lock:
            self._warm_inflight[profile] = max(0, self._warm_inflight.get(profile, 0) - to_create)
            self._warm_ready.setdefault(profile, []).extend(created)
            ready_count = len(self._warm_ready.get(profile, []))

        if created or errors:
            logger.info(
                "[DOCKER] Agent warm pool target=%d ready=%d created=%d errors=%d",
                target, ready_count, len(created), len(errors),
            )
        return {"target": target, "ready": ready_count, "created": len(created), "errors": errors}

    async def acquire_warm_container(
        self,
        config: SlotContainerConfig,
        *,
        image: str,
        cpu_limit: float,
        memory_limit: str,
        network: str | None,
    ) -> ContainerInfo | None:
        target = warm_pool_target_from_env()
        if target <= 0:
            return None

        profile = (image, cpu_limit, memory_limit, network)
        lock = self._get_warm_lock()
        async with lock:
            ready = self._warm_ready.setdefault(profile, [])
            container = ready.pop(0) if ready else None

        self._schedule_warm_refill(
            image=image,
            cpu_limit=cpu_limit,
            memory_limit=memory_limit,
            network=network,
            target_size=target,
        )
        if container is None:
            logger.warning(
                "[DOCKER] Agent warm pool empty for slot %s; falling back to cold start",
                config.slot_id,
            )
            return None
        logger.info(
            "[DOCKER] Checked out warm agent container %s for slot %s",
            container.container_id[:12], config.slot_id,
        )
        return container

    def _schedule_warm_refill(
        self,
        *,
        image: str,
        cpu_limit: float,
        memory_limit: str,
        network: str | None,
        target_size: int,
    ) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        profile = (image, cpu_limit, memory_limit, network)
        existing = self._warm_refill_tasks.get(profile)
        if existing is not None and not existing.done():
            return
        self._warm_refill_tasks[profile] = loop.create_task(
            self.prewarm(
                image=image,
                cpu_limit=cpu_limit,
                memory_limit=memory_limit,
                network=network,
                target_size=target_size,
            )
        )

    async def _create_warm_container(
        self,
        *,
        image: str,
        cpu_limit: float,
        memory_limit: str,
        network: str | None,
    ) -> ContainerInfo:
        warm_id = uuid.uuid4().hex[:12]
        labels = {
            "agenthub.warm": "true",
            "agenthub.warm.pool": "agent-slot",
            "agenthub.warm.id": warm_id,
        }
        return await self._create_agent_container(
            image=image,
            name=f"developer-hub-agent-warm-{os.getpid()}-{warm_id}",
            environment={
                "AGENTHUB_WARM_CONTAINER": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUNBUFFERED": "1",
            },
            labels=labels,
            cpu_limit=cpu_limit,
            memory_limit=memory_limit,
            network=network,
            entrypoint=_WARM_IDLE_ENTRYPOINT,
        )

    async def run_agent_in_warm_container(
        self,
        container_id: str,
        config: SlotContainerConfig,
        *,
        timeout: float,
    ) -> ContainerRunResult:
        client = self._get_client()
        container = client.containers.get(container_id)
        env = {
            "SLOT_CONFIG": config.to_env_json(),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
        }

        def _exec_agent() -> ContainerRunResult:
            result = container.exec_run(
                ["python", "-m", "agent"],
                environment=env,
                workdir="/app",
                user="agentuser",
                stdout=True,
                stderr=True,
            )
            exit_code = int(getattr(result, "exit_code", result[0] if isinstance(result, tuple) else -1))
            output = getattr(result, "output", result[1] if isinstance(result, tuple) and len(result) > 1 else "")
            if isinstance(output, bytes):
                logs = output.decode("utf-8", errors="replace")
            else:
                logs = str(output or "")
            return ContainerRunResult(exit_code=exit_code, logs=logs)

        return await asyncio.wait_for(asyncio.to_thread(_exec_agent), timeout=timeout)

    async def cleanup_warm_pool(self) -> dict[str, Any]:
        lock = self._get_warm_lock()
        async with lock:
            containers = [container for ready in self._warm_ready.values() for container in ready]
            self._warm_ready.clear()
            tasks = list(self._warm_refill_tasks.values())
            self._warm_refill_tasks.clear()
        for task in tasks:
            if not task.done():
                task.cancel()
        removed = 0
        errors: list[str] = []
        for container in containers:
            try:
                await self.remove(container.container_id)
                removed += 1
            except Exception as exc:
                errors.append(str(exc))
        return {"removed": removed, "errors": errors}

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
