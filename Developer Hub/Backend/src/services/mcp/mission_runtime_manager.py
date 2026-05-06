"""Lifecycle and proxy manager for per-mission MCP runtime containers."""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from services.mcp.mcp_client_manager import MCPToolCallResult, _timeout_for_tool

logger = logging.getLogger(__name__)

_DEFAULT_IMAGE = "developer-hub-mcp-runtime:dev"
_DEFAULT_PORT = 8765
_DEFAULT_CPU = 2.0
_DEFAULT_MEMORY = "4g"
_DEFAULT_START_TIMEOUT = 120.0
_RUNTIME_KIND_LABEL = "agenthub.kind"
_RUNTIME_KIND_VALUE = "mission-mcp-runtime"
_RUNTIME_SESSION_LABEL = "agenthub.session_id"

_RUNTIME_BIND_DESTINATIONS = {
    "/app/data",
    "/app/src",
    "/opt/agenthub-mcp/mcp",
    "/opt/agenthub-mcp/external",
}
_RUNTIME_ENV_MOUNTS = {
    "MCP_RUNTIME_BACKEND_SRC_HOST_DIR": "/app/src",
    "MCP_RUNTIME_MCP_HOST_DIR": "/opt/agenthub-mcp/mcp",
    "MCP_RUNTIME_EXTERNAL_HOST_DIR": "/opt/agenthub-mcp/external",
}


def _parse_memory_limit(limit: str) -> int:
    text = limit.strip().lower()
    multipliers = {"k": 1024, "m": 1024**2, "g": 1024**3}
    if text[-1] in multipliers:
        return int(float(text[:-1]) * multipliers[text[-1]])
    return int(text)


def _safe_name(session_id: str) -> str:
    suffix = "".join(c if c.isalnum() or c in "_.-" else "-" for c in session_id[:36])
    return f"developer-hub-mcp-{suffix}"


def _schema_names(schemas: list[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for tool in schemas:
        name = tool.get("function", {}).get("name")
        if isinstance(name, str) and name:
            names.add(name)
    return names


def _runtime_environment(session_id: str, port: int) -> dict[str, str]:
    inherited_prefixes = (
        "MCP_",
        "BROWSER_VISUAL_",
        "OTEL_",
        "NODE_",
        "HTTP_",
        "HTTPS_",
        "NO_PROXY",
        "no_proxy",
        "http_proxy",
        "https_proxy",
    )
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if "TOKEN" in upper or "SECRET" in upper or "PASSWORD" in upper:
            continue
        if key.startswith(inherited_prefixes):
            env[key] = value
    env.update(
        {
            "AGENTHUB_MCP_RUNTIME_SESSION_ID": session_id,
            "MCP_RUNTIME_PORT": str(port),
            "MCP_CONFIG_PATH": os.environ.get("MCP_CONFIG_PATH", "/app/src/mcp_servers.json"),
            "MCP_REPO_DIR": os.environ.get("MCP_REPO_DIR", "/opt/agenthub-mcp"),
            "MCP_WORKSPACE_ROOT": os.environ.get("MCP_WORKSPACE_ROOT", "/opt/agenthub-mcp"),
            "PYTHONPATH": os.environ.get("MCP_RUNTIME_PYTHONPATH", "/app/src:/app"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "NODE_PATH": os.environ.get("NODE_PATH", "/usr/local/lib/node_modules"),
            "BROWSER_VISUAL_CHROMIUM_EXECUTABLE": os.environ.get(
                "BROWSER_VISUAL_CHROMIUM_EXECUTABLE", "/usr/bin/chromium"
            ),
        }
    )
    return env


def _runtime_readonly_mounts(client: Any) -> dict[str, dict[str, str]]:
    """Build explicit read-only binds for only the paths MCP tools need.

    The Docker daemon interprets bind sources on the host, not inside the
    backend container. We therefore inspect the backend container's bind mounts
    and re-use the host sources for the mission runtime instead of inheriting
    every backend mount via ``volumes_from``.
    """
    mounts: dict[str, dict[str, str]] = {}

    current_ref = _current_container_ref(client)
    if current_ref:
        try:
            current = client.containers.get(current_ref)
            for mount in current.attrs.get("Mounts", []):
                destination = mount.get("Destination")
                source = mount.get("Source")
                if destination in _RUNTIME_BIND_DESTINATIONS and source:
                    mounts[source] = {"bind": destination, "mode": "ro"}
        except Exception as exc:
            logger.warning("[MISSION_MCP] could not inspect backend bind mounts: %s", exc)

    for env_name, destination in _RUNTIME_ENV_MOUNTS.items():
        source = os.environ.get(env_name)
        if source:
            mounts[source] = {"bind": destination, "mode": "ro"}

    existing_destinations = {bind.get("bind") for bind in mounts.values()}
    for source, bind in _native_dev_runtime_mounts().items():
        if bind.get("bind") not in existing_destinations:
            mounts[source] = bind
            existing_destinations.add(bind.get("bind"))

    return mounts


def _native_dev_runtime_mounts() -> dict[str, dict[str, str]]:
    """Derive sidecar binds when the backend is run directly from the repo.

    Compose can inspect the backend container's own bind mounts. A native
    backend process cannot, but the Docker daemon still needs host paths for
    the per-mission MCP runtime. The repo layout gives us those paths.
    """
    src_dir = Path(__file__).resolve().parents[2]
    backend_dir = src_dir.parent
    developer_hub_dir = backend_dir.parent
    repo_root = developer_hub_dir.parent
    candidates = {
        src_dir: "/app/src",
        backend_dir / ".data": "/app/data",
        repo_root / "mcp": "/opt/agenthub-mcp/mcp",
        repo_root / "external": "/opt/agenthub-mcp/external",
    }
    mounts: dict[str, dict[str, str]] = {}
    for source, destination in candidates.items():
        if source.exists():
            mounts[str(source)] = {"bind": destination, "mode": "ro"}
    return mounts


def _current_container_id() -> str | None:
    return os.environ.get("AGENTHUB_CURRENT_CONTAINER_ID") or os.environ.get("HOSTNAME")


def _current_container_ref(client: Any) -> str | None:
    container_id = _current_container_id()
    if not container_id:
        return None
    try:
        client.containers.get(container_id)
        return container_id
    except Exception:
        return None


def _current_network(client: Any) -> str | None:
    configured = os.environ.get("MCP_RUNTIME_NETWORK") or os.environ.get("AGENT_NETWORK")
    if configured:
        return configured
    container_id = _current_container_id()
    if not container_id:
        return None
    try:
        current = client.containers.get(container_id)
        networks = current.attrs.get("NetworkSettings", {}).get("Networks", {})
        if networks:
            return next(iter(networks.keys()))
    except Exception as exc:
        logger.warning("[MISSION_MCP] could not inspect current container network: %s", exc)
    return None


def _container_ip(container: Any, preferred_network: str | None) -> str:
    container.reload()
    networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
    if preferred_network and preferred_network in networks:
        ip = networks[preferred_network].get("IPAddress")
        if ip:
            return ip
    for info in networks.values():
        ip = info.get("IPAddress")
        if ip:
            return ip
    raise RuntimeError("Mission MCP runtime container has no reachable IP address")


@dataclass
class MissionMCPProxyManager:
    """Manager-compatible proxy that forwards MCP calls to a mission sidecar."""

    session_id: str
    container_id: str
    container_name: str
    base_url: str
    schemas: list[dict[str, Any]]
    tool_server_map: dict[str, str]
    auth_token: str
    _docker_client: Any

    def has_tools(self) -> bool:
        return bool(self.schemas)

    def get_openai_tools_schema(self) -> list[dict[str, Any]]:
        return list(self.schemas)

    def qualified_name(self, tool_name: str) -> str:
        server = self.tool_server_map.get(tool_name)
        return f"{server or '<mission-runtime>'}::{tool_name}"

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict,
        tokens: dict[str, str] | None = None,
        *,
        allowed_tools: set[str] | frozenset[str] | None = None,
        workspace_id: str | None = None,
        execution_context: dict | None = None,
    ) -> str:
        result = await self.call_tool_with_metrics(
            tool_name,
            arguments,
            tokens,
            allowed_tools=allowed_tools,
            workspace_id=workspace_id,
            execution_context=execution_context,
        )
        return result.output

    async def call_tool_with_metrics(
        self,
        tool_name: str,
        arguments: dict,
        tokens: dict[str, str] | None = None,
        *,
        allowed_tools: set[str] | frozenset[str] | None = None,
        workspace_id: str | None = None,
        execution_context: dict | None = None,
    ) -> MCPToolCallResult:
        timeout_s = _timeout_for_tool(tool_name) + 15
        payload = {
            "tool_name": tool_name,
            "arguments": arguments or {},
            "tokens": tokens,
            "allowed_tools": sorted(allowed_tools) if allowed_tools is not None else None,
            "workspace_id": workspace_id,
            "execution_context": execution_context,
        }
        http_started = time.monotonic()
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s, connect=10.0)) as client:
            response = await client.post(
                f"{self.base_url}/tools/call",
                json=payload,
                headers={"X-AgentHub-MCP-Token": self.auth_token},
            )
        sidecar_http_ms = int((time.monotonic() - http_started) * 1000)
        if response.status_code != 200:
            detail = response.text[:1000]
            try:
                parsed = response.json()
                detail = str(parsed.get("detail") or parsed)
            except Exception:
                pass
            raise RuntimeError(f"Mission MCP runtime rejected {tool_name}: {detail}")
        body = response.json()
        latency_breakdown = dict(body.get("latency_breakdown_ms") or {})
        latency_breakdown["sidecarHttpMs"] = sidecar_http_ms
        return MCPToolCallResult(
            output=str(body.get("output", "")),
            latency_breakdown_ms=latency_breakdown,
        )

    async def close(self) -> None:
        def _remove() -> None:
            try:
                container = self._docker_client.containers.get(self.container_id)
                container.remove(force=True)
            except Exception as exc:
                logger.warning(
                    "[MISSION_MCP] failed to remove runtime container %s: %s",
                    self.container_id[:12],
                    exc,
                )

        await asyncio.to_thread(_remove)


async def cleanup_mission_mcp_runtimes(
    *,
    active_session_ids: set[str] | frozenset[str] | None = None,
    docker_client: Any | None = None,
) -> dict[str, Any]:
    """Remove mission MCP sidecars that no live in-process job owns.

    Per-mission runtimes intentionally run with ``auto_remove=False`` so startup
    failures and tool logs remain inspectable. That means a backend restart can
    strand a healthy sidecar after the in-memory job registry is gone. Ownership
    is therefore label based: only containers with the AgentHub mission-runtime
    label are considered, and currently active session ids are preserved.
    """
    if docker_client is None:
        try:
            import docker
        except Exception as exc:
            logger.warning("[MISSION_MCP] Docker SDK unavailable for runtime cleanup: %s", exc)
            return {"removed": 0, "skipped": 0, "errors": [str(exc)]}
        docker_client = docker.from_env()

    active = set(active_session_ids or set())
    removed = 0
    skipped = 0
    errors: list[str] = []

    def _cleanup() -> None:
        nonlocal removed, skipped
        containers = docker_client.containers.list(
            all=True,
            filters={"label": f"{_RUNTIME_KIND_LABEL}={_RUNTIME_KIND_VALUE}"},
        )
        for container in containers:
            labels = getattr(container, "labels", None)
            if labels is None:
                labels = container.attrs.get("Config", {}).get("Labels", {}) if getattr(container, "attrs", None) else {}
            labels = labels or {}
            session_id = labels.get(_RUNTIME_SESSION_LABEL)
            name = getattr(container, "name", "<unknown>")
            short_id = getattr(container, "short_id", getattr(container, "id", name))
            if session_id in active:
                skipped += 1
                continue
            try:
                container.remove(force=True)
                removed += 1
                logger.info(
                    "[MISSION_MCP] removed orphan runtime container %s (%s) session=%s",
                    short_id,
                    name,
                    session_id or "<missing>",
                )
            except Exception as exc:
                errors.append(f"{short_id}: {exc}")
                logger.warning(
                    "[MISSION_MCP] failed to remove orphan runtime container %s (%s): %s",
                    short_id,
                    name,
                    exc,
                )

    await asyncio.to_thread(_cleanup)
    return {"removed": removed, "skipped": skipped, "errors": errors}


async def _wait_until_ready(
    *,
    container: Any,
    base_url: str,
    expected_tools: set[str],
    timeout_s: float,
) -> None:
    deadline = time.monotonic() + timeout_s
    last_error = "not ready"
    async with httpx.AsyncClient(timeout=2.0) as client:
        while time.monotonic() < deadline:
            container.reload()
            if container.status == "exited":
                raw_logs = await asyncio.to_thread(container.logs, tail=200)
                logs = raw_logs.decode("utf-8", errors="replace") if isinstance(raw_logs, bytes) else str(raw_logs)
                raise RuntimeError(f"Mission MCP runtime exited during startup:\n{logs[-4000:]}")
            try:
                response = await client.get(f"{base_url}/health")
                if response.status_code == 200:
                    body = response.json()
                    actual_tools = set(body.get("tool_names") or [])
                    missing = sorted(expected_tools - actual_tools)
                    extra = sorted(actual_tools - expected_tools)
                    if missing or extra:
                        raise RuntimeError(
                            "Mission MCP runtime tool mismatch: "
                            f"missing={missing[:20]} extra={extra[:20]}"
                        )
                    return
                last_error = f"HTTP {response.status_code}: {response.text[:300]}"
            except RuntimeError:
                raise
            except Exception as exc:
                last_error = str(exc)
            await asyncio.sleep(0.5)
    raw_logs = await asyncio.to_thread(container.logs, tail=200)
    logs = raw_logs.decode("utf-8", errors="replace") if isinstance(raw_logs, bytes) else str(raw_logs)
    raise RuntimeError(
        f"Mission MCP runtime did not become healthy within {timeout_s:.0f}s: {last_error}\n{logs[-4000:]}"
    )


async def start_mission_mcp_runtime(session_id: str, source_manager: Any) -> MissionMCPProxyManager:
    """Create and validate a dedicated MCP runtime container for one mission."""
    try:
        import docker
    except Exception as exc:
        raise RuntimeError("Docker SDK is required for mission MCP runtime containers") from exc

    schemas = source_manager.get_openai_tools_schema()
    expected_tools = set(getattr(source_manager, "tools", {}) or _schema_names(schemas))
    if not expected_tools:
        raise RuntimeError("Cannot start mission MCP runtime: startup MCP catalog has zero tools")

    image = os.environ.get("MCP_RUNTIME_IMAGE", _DEFAULT_IMAGE)
    port = int(os.environ.get("MCP_RUNTIME_PORT", str(_DEFAULT_PORT)))
    cpu = float(os.environ.get("MCP_RUNTIME_CPUS", str(_DEFAULT_CPU)))
    memory = os.environ.get("MCP_RUNTIME_MEMORY", _DEFAULT_MEMORY)
    timeout_s = float(os.environ.get("MCP_RUNTIME_START_TIMEOUT_S", str(_DEFAULT_START_TIMEOUT)))
    name = _safe_name(session_id)

    client = docker.from_env()
    await asyncio.to_thread(client.ping)

    network = _current_network(client)
    volumes = _runtime_readonly_mounts(client)
    auth_token = secrets.token_urlsafe(32)

    labels = {
        _RUNTIME_KIND_LABEL: _RUNTIME_KIND_VALUE,
        _RUNTIME_SESSION_LABEL: session_id,
    }
    env = _runtime_environment(session_id, port)
    env["MCP_RUNTIME_AUTH_TOKEN"] = auth_token
    command = ["python3", "-m", "services.mcp.mission_runtime_service"]

    def _create_container() -> Any:
        try:
            old = client.containers.get(name)
            old.remove(force=True)
        except Exception:
            pass
        return client.containers.create(
            image=image,
            name=name,
            command=command,
            environment=env,
            labels=labels,
            nano_cpus=int(cpu * 1e9),
            mem_limit=_parse_memory_limit(memory),
            memswap_limit=_parse_memory_limit(memory),
            pids_limit=300,
            network=network,
            volumes=volumes,
            read_only=True,
            tmpfs={
                "/tmp": "rw,nosuid,nodev,noexec,size=512m,mode=1777",
                "/home/appuser/.cache": "rw,exec,nosuid,nodev,size=512m,uid=1000,gid=1000,mode=755",
                "/home/appuser/.config": "rw,nosuid,nodev,size=128m,uid=1000,gid=1000,mode=755",
                "/home/appuser/.local": "rw,exec,nosuid,nodev,size=512m,uid=1000,gid=1000,mode=755",
                "/home/appuser/.npm": "rw,exec,nosuid,nodev,size=512m,uid=1000,gid=1000,mode=755",
            },
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            privileged=False,
            init=True,
            healthcheck={"test": ["NONE"]},
            detach=True,
            auto_remove=False,
        )

    container = await asyncio.to_thread(_create_container)
    try:
        await asyncio.to_thread(container.start)
        ip = _container_ip(container, network)
        base_url = f"http://{ip}:{port}"
        await _wait_until_ready(
            container=container,
            base_url=base_url,
            expected_tools=expected_tools,
            timeout_s=timeout_s,
        )
    except Exception:
        try:
            await asyncio.to_thread(container.remove, force=True)
        except Exception:
            pass
        raise

    logger.info(
        "[MISSION_MCP] started runtime container %s (%s) for session %s tools=%d",
        container.short_id,
        name,
        session_id,
        len(expected_tools),
    )
    return MissionMCPProxyManager(
        session_id=session_id,
        container_id=container.id,
        container_name=name,
        base_url=base_url,
        schemas=schemas,
        tool_server_map=dict(getattr(source_manager, "tool_server_map", {}) or {}),
        auth_token=auth_token,
        _docker_client=client,
    )