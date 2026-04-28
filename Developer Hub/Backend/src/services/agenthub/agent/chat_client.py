"""Pluggable chat-completions client used inside the agent container.

The agent Docker image (``Dockerfile.agent``) intentionally stays
minimal and does **not** include the Microsoft Agent Framework
(``agent_framework``) dependency — the orchestration layer owns that.
Inside the container we only need a thin chat-completions abstraction
so we can target GitHub Copilot (default), Azure OpenAI, or Microsoft
Foundry with no code changes, just a different env var.

Selection is driven by ``AGENT_CHAT_CLIENT``:

* ``copilot``      — GitHub Copilot chat completions (default)
* ``azure_openai`` — Azure OpenAI chat completions endpoint
* ``foundry``      — Microsoft Foundry (OpenAI-compatible) endpoint

The alternative clients read their endpoints from env vars at
construction time and raise a clear ``ValueError`` when mandatory
settings are missing — no silent fallbacks.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Protocol

import httpx

logger = logging.getLogger("agent.chat_client")

COPILOT_API_BASE = "https://api.githubcopilot.com"
DEFAULT_TIMEOUT = 60  # seconds


class ChatClient(Protocol):
    """Minimal chat completions contract."""

    async def chat(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        model: str,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict:
        """Return an OpenAI-style ``{"choices": [{"message": {...}}]}``
        response body. Raises on non-2xx HTTP responses."""
        ...


class CopilotChatClient:
    """GitHub Copilot chat completions."""

    def __init__(self, token: str) -> None:
        if not token:
            raise ValueError("CopilotChatClient requires a non-empty token")
        self._token = token

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Copilot-Integration-Id": "vscode-chat",
            "Editor-Version": "vscode/1.100.0",
            "Editor-Plugin-Version": "copilot-chat/0.25.0",
        }

    async def chat(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        model: str,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": 4096,
            "temperature": 0.4,
            "stream": False,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        async with httpx.AsyncClient(timeout=timeout) as http:
            resp = await http.post(
                f"{COPILOT_API_BASE}/chat/completions",
                json=body, headers=self._headers(),
            )
            if resp.status_code != 200:
                raise RuntimeError(
                    f"Copilot HTTP {resp.status_code}: {resp.text[:200]}",
                )
            return resp.json()


class OpenAICompatibleChatClient:
    """Shared transport for Azure OpenAI / Foundry / any
    OpenAI-compatible endpoint. Handles both ``api-key`` (Azure) and
    ``Authorization: Bearer`` header styles."""

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        label: str,
        auth_style: str = "bearer",  # "bearer" or "api-key"
    ) -> None:
        if not endpoint:
            raise ValueError(f"{label}ChatClient requires an endpoint URL")
        if not api_key:
            raise ValueError(f"{label}ChatClient requires an API key")
        self._endpoint = endpoint.rstrip("/")
        self._api_key = api_key
        self._label = label
        self._auth_style = auth_style

    def _headers(self) -> dict:
        if self._auth_style == "api-key":
            return {"api-key": self._api_key, "Content-Type": "application/json"}
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    async def chat(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        model: str,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": 4096,
            "temperature": 0.4,
            "stream": False,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        async with httpx.AsyncClient(timeout=timeout) as http:
            resp = await http.post(
                f"{self._endpoint}/chat/completions",
                json=body, headers=self._headers(),
            )
            if resp.status_code != 200:
                raise RuntimeError(
                    f"{self._label} HTTP {resp.status_code}: {resp.text[:200]}",
                )
            return resp.json()


def make_chat_client(copilot_token: str) -> ChatClient:
    """Env-driven factory. Default = ``copilot``.

    Env vars consumed:
    * ``AGENT_CHAT_CLIENT``      — ``copilot`` | ``azure_openai`` | ``foundry``
    * ``AZURE_OPENAI_ENDPOINT``  — required for ``azure_openai``
    * ``AZURE_OPENAI_API_KEY``   — required for ``azure_openai``
    * ``FOUNDRY_ENDPOINT``       — required for ``foundry``
    * ``FOUNDRY_API_KEY``        — required for ``foundry``
    """
    kind = (os.environ.get("AGENT_CHAT_CLIENT") or "copilot").strip().lower()
    if kind == "copilot":
        return CopilotChatClient(token=copilot_token)
    if kind == "azure_openai":
        return OpenAICompatibleChatClient(
            endpoint=os.environ.get("AZURE_OPENAI_ENDPOINT", ""),
            api_key=os.environ.get("AZURE_OPENAI_API_KEY", ""),
            label="AzureOpenAI",
            auth_style="api-key",
        )
    if kind == "foundry":
        return OpenAICompatibleChatClient(
            endpoint=os.environ.get("FOUNDRY_ENDPOINT", ""),
            api_key=os.environ.get("FOUNDRY_API_KEY", ""),
            label="Foundry",
            auth_style="bearer",
        )
    raise ValueError(
        f"Unknown AGENT_CHAT_CLIENT={kind!r}; expected "
        "'copilot', 'azure_openai' or 'foundry'.",
    )
