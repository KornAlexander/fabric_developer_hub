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

import asyncio
import logging
import json
import os
from collections.abc import Awaitable, Callable
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
        tool_choice: dict | str | None = None,
        model: str,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict:
        """Return an OpenAI-style ``{"choices": [{"message": {...}}]}``
        response body. Raises on non-2xx HTTP responses."""
        ...

    async def chat_stream(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        tool_choice: dict | str | None = None,
        model: str,
        timeout: float = DEFAULT_TIMEOUT,
        on_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict:
        """Streaming variant of :meth:`chat`. Emits text deltas as soon
        as the provider sends them, then returns the same OpenAI-style
        response shape as the non-streaming call."""
        ...


def _base_body(
    messages: list[dict],
    *,
    tools: list[dict] | None,
    tool_choice: dict | str | None,
    model: str,
    stream: bool,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": 4096,
        "temperature": 0.4,
        "stream": stream,
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = tool_choice or "auto"
    return body


def _merge_tool_call_delta(tool_calls: list[dict[str, Any]], delta_tool_call: dict[str, Any]) -> None:
    index = int(delta_tool_call.get("index") or 0)
    while len(tool_calls) <= index:
        tool_calls.append({"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
    target = tool_calls[index]
    if delta_tool_call.get("id"):
        target["id"] = delta_tool_call["id"]
    if delta_tool_call.get("type"):
        target["type"] = delta_tool_call["type"]
    function_delta = delta_tool_call.get("function") or {}
    function_target = target.setdefault("function", {"name": "", "arguments": ""})
    name_delta = function_delta.get("name")
    if isinstance(name_delta, str) and name_delta:
        current = str(function_target.get("name") or "")
        if not current:
            function_target["name"] = name_delta
        elif name_delta != current and not current.endswith(name_delta):
            function_target["name"] = f"{current}{name_delta}"
    args_delta = function_delta.get("arguments")
    if isinstance(args_delta, str) and args_delta:
        function_target["arguments"] = f"{function_target.get('arguments') or ''}{args_delta}"


async def stream_chat_completion(
    *,
    url: str,
    body: dict[str, Any],
    headers: dict[str, str],
    timeout: float,
    label: str,
    on_delta: Callable[[str], Awaitable[None]] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict:
    """Call an OpenAI-compatible chat-completions endpoint in streaming
    mode and reconstruct a normal completion response.

    The Copilot/OpenAI stream sends ``data: {...}`` SSE lines with
    ``choices[].delta`` fragments. We forward content fragments to the
    UI immediately and accumulate tool-call name/argument fragments so
    the existing agent loop can keep using the non-streaming response
    shape for tool execution.
    """
    stream_body = {**body, "stream": True}
    assistant: dict[str, Any] = {"role": "assistant", "content": ""}
    tool_calls: list[dict[str, Any]] = []
    finish_reason: str | None = None

    async with httpx.AsyncClient(timeout=timeout) as http:
        async with http.stream("POST", url, json=stream_body, headers=headers) as resp:
            if resp.status_code != 200:
                error_body = await resp.aread()
                raise RuntimeError(f"{label} HTTP {resp.status_code}: {error_body.decode(errors='ignore')[:200]}")
            async for line in resp.aiter_lines():
                if should_cancel and should_cancel():
                    raise asyncio.CancelledError("cancelled mid-LLM stream")
                if not line or not line.startswith("data:"):
                    continue
                raw = line.removeprefix("data:").strip()
                if not raw or raw == "[DONE]":
                    break
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    logger.debug("[%s] Skipping malformed stream payload: %s", label, raw[:120])
                    continue
                choice = (payload.get("choices") or [{}])[0]
                finish_reason = choice.get("finish_reason") or finish_reason
                delta = choice.get("delta") or {}
                if delta.get("role"):
                    assistant["role"] = delta["role"]
                content_delta = delta.get("content")
                if isinstance(content_delta, str) and content_delta:
                    assistant["content"] = f"{assistant.get('content') or ''}{content_delta}"
                    if on_delta:
                        await on_delta(content_delta)
                for tool_delta in delta.get("tool_calls") or []:
                    if isinstance(tool_delta, dict):
                        _merge_tool_call_delta(tool_calls, tool_delta)

    if tool_calls:
        assistant["tool_calls"] = tool_calls
    if not assistant.get("content"):
        assistant["content"] = None
    return {"choices": [{"message": assistant, "finish_reason": finish_reason or "stop", "index": 0}]}


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
        tool_choice: dict | str | None = None,
        model: str,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict:
        body = _base_body(messages, tools=tools, tool_choice=tool_choice, model=model, stream=False)

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

    async def chat_stream(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        tool_choice: dict | str | None = None,
        model: str,
        timeout: float = DEFAULT_TIMEOUT,
        on_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict:
        body = _base_body(messages, tools=tools, tool_choice=tool_choice, model=model, stream=True)
        return await stream_chat_completion(
            url=f"{COPILOT_API_BASE}/chat/completions",
            body=body,
            headers=self._headers(),
            timeout=timeout,
            label="Copilot",
            on_delta=on_delta,
        )


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
        tool_choice: dict | str | None = None,
        model: str,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict:
        body = _base_body(messages, tools=tools, tool_choice=tool_choice, model=model, stream=False)

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

    async def chat_stream(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        tool_choice: dict | str | None = None,
        model: str,
        timeout: float = DEFAULT_TIMEOUT,
        on_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict:
        body = _base_body(messages, tools=tools, tool_choice=tool_choice, model=model, stream=True)
        return await stream_chat_completion(
            url=f"{self._endpoint}/chat/completions",
            body=body,
            headers=self._headers(),
            timeout=timeout,
            label=self._label,
            on_delta=on_delta,
        )


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
