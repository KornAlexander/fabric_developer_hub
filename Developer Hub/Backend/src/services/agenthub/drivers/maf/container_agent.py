"""ContainerAgent — a MAF ``BaseAgent`` backed by ``SlotRunner``.

This is the keystone adapter for the MAF migration. Each slot in a
Composition is wrapped as one ``ContainerAgent``. MAF's
``SequentialBuilder`` (and every other topology builder) sees it as
an ordinary agent, while under the hood each ``run()`` invocation
actually spawns an isolated Docker container via
``ContainerSlotRunner`` — preserving our security model and tool
runtime chokepoint.

Design notes
------------
* We do **not** depend on ``agent_framework`` at import time. The
  ``BaseAgent`` parent is resolved lazily the first time
  ``make_container_agent`` is called. This keeps the Backend
  importable on deployments that haven't installed the optional
  dependency.
* MAF's shared conversation (``list[Message]``) replaces our
  hand-rolled ``HandoffPayload`` chain for the sequential topology.
  When MAF invokes our ``run()``, we extract the latest message
  text as the upstream context and pass it to ``SlotRunner`` as a
  synthetic upstream handoff. The slot's final output becomes a new
  assistant ``Message`` appended to the shared conversation.
* The ``slot_id`` is captured at construction time so the agent
  binds 1:1 to a composition slot.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from services.agenthub.drivers.handoff import HandoffPayload
from services.agenthub.drivers.maf.availability import ensure_agent_framework_version

if TYPE_CHECKING:
    from services.agenthub.drivers.slot_runner import SlotRunner

logger = logging.getLogger(__name__)

# Module-level cache: the resolved MAF ``BaseAgent`` class + helpers
# are assigned on first use, then reused. Kept as a dict rather than
# module globals so the lazy-import path is a single function.
_MAF_TYPES: dict[str, Any] = {}


def _load_maf_types() -> dict[str, Any]:
    """Import MAF primitives lazily and cache them.

    Raises ``ImportError`` if ``agent_framework`` isn't installed.
    Callers must gate on ``maf_available()`` first.
    """
    if _MAF_TYPES:
        return _MAF_TYPES
    # Direct imports — ImportError here is intentional: the caller
    # must check ``maf_available()`` before instantiating agents.
    ensure_agent_framework_version()
    try:
        from agent_framework import (  # type: ignore[import-not-found]
            AgentResponse,
            AgentResponseUpdate,
            BaseAgent,
            Content,
            Message,
        )
    except ImportError:
        from agent_framework._agents import (  # type: ignore[import-not-found]
            AgentResponse,
            AgentResponseUpdate,
            BaseAgent,
        )
        from agent_framework._types import Content, Message  # type: ignore[import-not-found]

    _MAF_TYPES.update(
        BaseAgent=BaseAgent,
        Message=Message,
        Content=Content,
        AgentResponse=AgentResponse,
        AgentResponseUpdate=AgentResponseUpdate,
    )
    return _MAF_TYPES


def _extract_upstream_text(messages: Any, Message: Any) -> str:
    """Pull the most recent non-system message text from a MAF input.

    Accepts any of the forms MAF passes into ``run()``:
    - ``None``
    - a plain ``str``
    - a single ``Message``
    - a ``list`` of ``Message`` / ``str``

    Returns the concatenated text of the final non-system entry, or
    an empty string if nothing useful is there.
    """
    if messages is None:
        return ""
    if isinstance(messages, str):
        return messages
    if isinstance(messages, Message):
        return _message_text(messages)
    if isinstance(messages, list):
        for item in reversed(messages):
            if isinstance(item, str):
                return item
            if isinstance(item, Message):
                role = getattr(item, "role", None)
                if role == "system":
                    continue
                return _message_text(item)
    return ""


def _message_text(msg: Any) -> str:
    """Best-effort extraction of plain text from a MAF ``Message``.

    MAF messages carry a ``contents`` list of ``Content`` objects
    (TextContent, etc.). We flatten to a single string. If the
    shape ever changes we fall back to ``str(msg)``.
    """
    try:
        contents = getattr(msg, "contents", None)
        if contents is None:
            text = getattr(msg, "text", None)
            return text or ""
        out: list[str] = []
        for c in contents:
            t = getattr(c, "text", None) or (c if isinstance(c, str) else None)
            if t:
                out.append(t)
        return "\n".join(out).strip()
    except Exception:  # defensive — never crash the workflow
        return str(msg)


def make_container_agent(
    *,
    slot_id: str,
    role: str,
    agent_name: str,
    slot_runner: SlotRunner,
) -> Any:
    """Factory producing a ``BaseAgent`` subclass instance bound to one slot.

    Must only be called when ``maf_available()`` is ``True``. Returns
    an object compatible with MAF's ``SupportsAgentRun`` protocol,
    ready to be passed to ``SequentialBuilder(participants=[...])``.
    """
    types = _load_maf_types()
    BaseAgent = types["BaseAgent"]
    Message = types["Message"]
    Content = types["Content"]
    AgentResponse = types["AgentResponse"]
    AgentResponseUpdate = types["AgentResponseUpdate"]

    class ContainerAgent(BaseAgent):  # type: ignore[misc, valid-type]
        """MAF agent delegating to an isolated container per invocation.

        Each call to ``run()`` invokes ``SlotRunner.run_slot`` for the
        bound ``slot_id``. The slot's handoff summary is wrapped as an
        assistant message and appended to the MAF conversation.
        """

        def __init__(self) -> None:
            # BaseAgent typically accepts ``id``/``name``. We pass both
            # via kwargs with a defensive fallback if the MAF version
            # drops or renames them.
            try:
                super().__init__(id=slot_id, name=agent_name)  # type: ignore[call-arg]
            except TypeError:
                super().__init__()  # type: ignore[call-arg]
            self._slot_id = slot_id
            self._role = role
            self._runner = slot_runner
            self._name = agent_name

        # MAF's ``AgentExecutor`` (used by ``SequentialBuilder``)
        # *always* calls the streaming path and reassembles an
        # ``AgentResponse`` via ``AgentResponse.from_updates``. We must
        # therefore yield ``AgentResponseUpdate`` instances — yielding
        # a plain ``AgentResponse`` triggers the real bug we saw:
        #   ``AttributeError: 'AgentResponse' object has no attribute 'author_name'``.
        def run(  # type: ignore[override]
            self,
            messages: Any = None,
            *,
            stream: bool = False,
            **kwargs: Any,
        ) -> Any:
            if stream:
                return self._run_stream(messages)
            return self._run_once(messages)

        async def _invoke_slot(self, messages: Any) -> str | None:
            """Shared body: run the slot and return the assistant text.

            Returns ``None`` when the slot short-circuits (cancelled or
            budget exhausted). The streaming wrapper uses that signal
            to end the conversation immediately so MAF's runner stops
            looping over a now-dead workflow graph.
            """
            # If a previous slot already exhausted the budget, abort the
            # whole graph instead of cycling through every remaining edge.
            if self._runner._budget.check_budget():
                logger.info(
                    "[MAF:%s] Budget exhausted before slot=%s — halting agent",
                    self._name, self._slot_id,
                )
                return None
            if self._runner._execution.cancel_event.is_set():
                logger.info(
                    "[MAF:%s] Cancelled before slot=%s — halting agent",
                    self._name, self._slot_id,
                )
                return None

            upstream_text = _extract_upstream_text(messages, Message)
            upstream_handoffs = (
                [_synthetic_upstream_handoff(self._slot_id, upstream_text)]
                if upstream_text
                else None
            )
            logger.info(
                "[MAF:%s] Running slot=%s role=%s upstream_len=%d",
                self._name, self._slot_id, self._role, len(upstream_text),
            )
            result = await self._runner.run_slot(
                self._slot_id,
                upstream_handoffs=upstream_handoffs,
                step_label=self._role,
            )
            status = getattr(result, "status", None)
            if status in ("budget_exhausted", "cancelled"):
                # Don't trip cancel_event — that would re-classify the
                # whole job as CANCELLED. Returning None is enough: the
                # workflow runner sees no new message and the cycle
                # converges naturally on the next superstep.
                logger.info(
                    "[MAF:%s] slot=%s status=%s — halting agent (no further handoffs)",
                    self._name, self._slot_id, status,
                )
                return None
            return _result_to_text(result, self._runner, self._slot_id)

        async def _run_once(self, messages: Any) -> Any:
            summary = await self._invoke_slot(messages)
            if summary is None:
                return AgentResponse(messages=[])
            text_content = Content.from_text(summary)
            msg = Message(
                role="assistant",
                contents=[text_content],
                author_name=self._name,
            )
            return AgentResponse(messages=[msg])

        async def _run_stream(self, messages: Any):
            """Yield a single terminal ``AgentResponseUpdate``.

            MAF's ``AgentExecutor._run_agent_streaming`` iterates this
            generator and calls ``AgentResponse.from_updates`` which
            expects every item to have ``author_name`` — hence this
            must be ``AgentResponseUpdate``, not ``AgentResponse``.

            When the slot short-circuits (budget/cancel) we yield
            nothing so the workflow runner sees no new message to
            route, terminating cycles cleanly instead of looping until
            ``WorkflowConvergenceException``.
            """
            summary = await self._invoke_slot(messages)
            if summary is None:
                return
            text_content = Content.from_text(summary)
            yield AgentResponseUpdate(
                contents=[text_content],
                role="assistant",
                author_name=self._name,
            )

    return ContainerAgent()


def _synthetic_upstream_handoff(slot_id: str, text: str) -> HandoffPayload:
    """Wrap arbitrary upstream text as a ``HandoffPayload``.

    ``SlotRunner.run_slot`` expects structured handoffs; the MAF path
    carries context as free-form messages. We manufacture the minimal
    payload so downstream injection still works.
    """
    import json

    marker_start = "===HANDOFF_START==="
    marker_end = "===HANDOFF_END==="
    if marker_start in text and marker_end in text:
        raw_json = text.split(marker_start, 1)[1].split(marker_end, 1)[0].strip()
        try:
            parsed = json.loads(raw_json)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            status = parsed.get("status") if parsed.get("status") in {"success", "partial", "error"} else "success"
            artifacts = parsed.get("artifacts") if isinstance(parsed.get("artifacts"), list) else []
            key_outputs = parsed.get("key_outputs") if isinstance(parsed.get("key_outputs"), dict) else {}
            summary = parsed.get("summary") if isinstance(parsed.get("summary"), str) else text[:2000]
            return HandoffPayload(
                from_slot_id=str(parsed.get("from") or "__maf_upstream__")[:64],
                to_slot_id=slot_id,
                kind="handoff",
                status=status,
                summary=summary[:2000],
                artifacts=artifacts,
                key_outputs={str(k): str(v) for k, v in key_outputs.items()},
            )

    return HandoffPayload(
        from_slot_id="__maf_upstream__",
        to_slot_id=slot_id,
        kind="handoff",
        status="success",
        summary=text[:2000],
    )


def _result_to_text(result: Any, runner: SlotRunner, slot_id: str) -> str:
    """Derive the assistant message body from a ``SlotResult``.

    Emits a **structured** handoff — a JSON block fenced with markers
    so downstream agents (and the legacy ``HandoffExtractor``) can
    parse it deterministically instead of regex-sniffing free text.

    Format::

        ===HANDOFF_START===
        {"from": "...", "role": "...", "status": "...",
         "summary": "...", "artifacts": [...], "key_outputs": {...}}
        ===HANDOFF_END===

        <human-readable summary text>

    The human-readable block preserves legacy parity so un-upgraded
    consumers still see a usable message.
    """
    import json

    status = getattr(result, "status", "unknown")
    role = getattr(result, "role", None) or ""
    artifacts = getattr(result, "artifacts", None) or []
    key_outputs = getattr(result, "key_outputs", None) or {}

    # Start with the legacy-compatible summary extraction so the
    # "summary" field carries exactly what the existing extractor
    # would have produced.
    summary_text = f"[slot {slot_id} status={status}]"
    try:
        payload = runner.extract_handoff(slot_id, "__maf_downstream__", "handoff")
        summary_text = payload.summary or summary_text
        artifacts = payload.artifacts
        key_outputs = payload.key_outputs
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("[MAF] Handoff extract failed for slot %s: %s", slot_id, exc)

    structured = {
        "from": slot_id,
        "role": role,
        "status": str(status),
        "summary": summary_text,
        "artifacts": list(artifacts) if isinstance(artifacts, (list, tuple)) else [],
        "key_outputs": key_outputs if isinstance(key_outputs, dict) else {},
    }
    try:
        payload_json = json.dumps(structured, default=str, ensure_ascii=False)
    except (TypeError, ValueError) as exc:  # pragma: no cover
        logger.warning("[MAF] Failed to JSON-encode handoff: %s", exc)
        payload_json = json.dumps({
            "from": slot_id, "role": role, "status": str(status),
            "summary": summary_text,
        })

    return (
        "===HANDOFF_START===\n"
        f"{payload_json}\n"
        "===HANDOFF_END===\n\n"
        f"{summary_text}"
    )
