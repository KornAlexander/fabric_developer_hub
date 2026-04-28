"""ComposeService — the single LLM analysis step that replaces plan
generation.

Takes (task prompt, attachments, architecture catalog, agent+skill
catalog, optional preferred architecture) and returns a
``Composition`` — the complete description of how to execute the task.
No intermediate "Plan" artifact is produced.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

import httpx

from domain.catalogs.architectures import (
    ARCHITECTURES,
    ARCHITECTURES_BY_ID,
)
from domain.models.agent_models import AgentTemplate
from domain.models.composition import (
    AgentSlot,
    Budget,
    Composition,
    CompositionError,
    Handoff,
    SkillRef,
)
from services.agenthub.agent_registry import AGENT_TEMPLATES, list_templates
from services.agenthub.attachments import ATTACHMENT_SHIELD_PROMPT, process_attachments
from services.agenthub.compose import RECIPES, build_system_prompt
from services.agenthub.compose_models import COMPOSE_FALLBACK_MODEL
from services.agenthub.workspace_context_service import WorkspaceContext

logger = logging.getLogger(__name__)

_INTERNAL_CONTROL_AGENT_IDS = {"orchestrator"}


COPILOT_API_BASE = "https://api.githubcopilot.com"
# Default model when the caller doesn't specify one. The runtime picks
# the actual model from the user's available catalog via
# ``compose_models.pick_compose_model`` — this constant is only used as
# the last-resort fallback. Kept here for backwards compatibility with
# tests that monkeypatch ``COMPOSE_MODEL``.
COMPOSE_MODEL = COMPOSE_FALLBACK_MODEL
COMPOSE_TIMEOUT_S = 60

# Module-level HTTP client reused across all compose calls. Opening a
# fresh client per request costs ~100–300ms in TCP+TLS handshake against
# api.githubcopilot.com; the shared client keeps the connection warm in
# the pool. Initialised lazily on first use.
_http_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    """Return the shared httpx client, creating it on first use."""
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            timeout=COMPOSE_TIMEOUT_S,
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        )
    return _http_client


# Common English synonyms the compose LLM occasionally emits instead of
# our canonical architecture ids. Mapping here lets the parser accept
# them silently rather than triggering a repair retry — which doubles
# compose latency and burns ~2k tokens per miss.
_ARCHITECTURE_ALIASES: dict[str, str] = {
    "orchestrator": "dynamic",
    "orchestration": "dynamic",
    "fanout": "dynamic",
    "fan-out": "dynamic",
    "map-reduce": "dynamic",
    "mapreduce": "dynamic",
    "parallel": "dynamic",
    "router": "dynamic",
    "magentic": "dynamic",
    "debate": "dynamic",
    "pipeline": "dynamic",
    "chain": "dynamic",
    "single": "dynamic",
    "solo": "dynamic",
    "supervisor": "dynamic",
    "sequential": "dynamic",
    "hierarchical": "dynamic",
    "reflection": "dynamic",
    "mixed": "dynamic",
    "network": "dynamic",
    "actor-critic": "dynamic",
}


import re as _re

# Regex that matches ```json ... ``` or ``` ... ``` fenced blocks.
_MD_FENCE_RE = _re.compile(
    r"```(?:json)?\s*\n?(.*?)\n?\s*```",
    _re.DOTALL,
)


def _extract_json(raw: str) -> str:
    """Best-effort extraction of a JSON object from LLM output.

    Handles three common failure modes:
    1. Empty / whitespace-only response
    2. Markdown-fenced JSON (```json ... ```)
    3. Prose before or after the JSON object

    Returns the cleaned string ready for ``json.loads``.
    Raises ``CompositionError`` if no JSON object can be found.
    """
    text = raw.strip()
    if not text:
        raise CompositionError("LLM returned empty response")

    # Already starts with { — try fast path, fall through if extra data
    if text.startswith("{"):
        try:
            json.loads(text)
            return text
        except json.JSONDecodeError:
            pass  # Fall through to brace extraction

    # Strip markdown fences
    fence_match = _MD_FENCE_RE.search(text)
    if fence_match:
        text = fence_match.group(1).strip()
        if text.startswith("{"):
            return text

    # Find the first { and last } — extract the object
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        candidate = text[first_brace:last_brace + 1]
        # Quick sanity check: try to parse it
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass  # Fall through to return original

    # If raw looks like it *could* be JSON (just with leading whitespace
    # or BOM), return it stripped and let json.loads produce a precise error.
    return text


SYSTEM_PROMPT_HEADER_MARKER = "You are the AgentHub Composer"


def _log_composition_result(
    correlation_id: str,
    comp,
    *,
    retry: bool = False,
) -> None:
    """Log the parsed Composition in a structured, human-readable format."""
    tag = "RETRY RESULT" if retry else "RESULT"
    logger.info(
        "[COMPOSE][%s] ── %s ──────────────────────────",
        correlation_id, tag,
    )
    logger.info(
        "[COMPOSE][%s] architecture: %s | slots: %d | handoffs: %d | entrypoint: %s",
        correlation_id,
        comp.architecture,
        len(comp.slots),
        len(comp.handoffs),
        comp.entrypoint_slot_id,
    )
    logger.info(
        "[COMPOSE][%s] headline: %s",
        correlation_id, comp.headline,
    )
    logger.info(
        "[COMPOSE][%s] rationale: %s",
        correlation_id, comp.rationale,
    )
    for i, slot in enumerate(comp.slots):
        skills = ", ".join(s.name for s in slot.skills) if slot.skills else "(none)"
        logger.info(
            "[COMPOSE][%s] slot[%d]: id=%s agent=%s role=%.100s skills=[%s]",
            correlation_id, i, slot.id, slot.agent_id, slot.role, skills,
        )
    for h in comp.handoffs:
        logger.info(
            "[COMPOSE][%s] handoff: %s → %s (kind=%s%s)",
            correlation_id, h.from_, h.to, h.kind,
            f", condition={h.condition}" if h.condition else "",
        )
    logger.info(
        "[COMPOSE][%s] budget: turns=%d tools=%d wallclock=%ds approvals=%s",
        correlation_id,
        comp.budget.max_turns,
        comp.budget.max_tool_calls,
        comp.budget.max_wallclock_s,
        comp.budget.require_approvals,
    )


class ComposeService:
    """Produces a Composition from a user task. One LLM call, one retry
    on schema failure, then raise ``CompositionError``."""

    def __init__(
        self,
        agents: dict[str, AgentTemplate] | None = None,
    ):
        self._agents = agents if agents is not None else AGENT_TEMPLATES

    async def compose(
        self,
        task_description: str,
        workspace_id: str,
        copilot_token: str,
        *,
        session_id: str | None = None,
        attachments: list[Any] | None = None,
        preferred_architecture: str | None = None,
        require_approvals: bool = True,
        branch_out: bool = False,
        model: str | None = None,
        workspace_context: WorkspaceContext | None = None,
    ) -> Composition:
        """Make the single compose call and return a Composition.

        ``preferred_architecture`` is the "Regenerate as …" override.
        ``model`` overrides the default composer model; validated against
        the user's catalog by the caller.
        """
        correlation_id = uuid.uuid4().hex[:12]
        session_id = session_id or str(uuid.uuid4())
        chosen_model = model or COMPOSE_MODEL

        # ── Log the input ────────────────────────────────────────
        logger.info(
            "[COMPOSE][%s] ── INPUT ──────────────────────────────────",
            correlation_id,
        )
        logger.info(
            "[COMPOSE][%s] task: %.500s",
            correlation_id, task_description,
        )
        if preferred_architecture:
            logger.info(
                "[COMPOSE][%s] preferred_architecture: %s",
                correlation_id, preferred_architecture,
            )
        logger.info(
            "[COMPOSE][%s] workspace: %s | model: %s | approvals: %s | branch_out: %s",
            correlation_id, workspace_id, chosen_model, require_approvals, branch_out,
        )

        att_dicts: list[dict] = []
        for a in attachments or []:
            att_dicts.append(a.model_dump() if hasattr(a, "model_dump") else a)

        if att_dicts:
            for i, att in enumerate(att_dicts):
                name = att.get("name", "?")
                kind = att.get("kind", "?")
                content_len = len(att.get("content", ""))
                logger.info(
                    "[COMPOSE][%s] attachment[%d]: %s (kind=%s, %d chars)",
                    correlation_id, i, name, kind, content_len,
                )

        text_block, image_parts, att_warnings = process_attachments(att_dicts)
        if att_warnings:
            logger.info(
                "[COMPOSE][%s] attachment warnings: %s",
                correlation_id, att_warnings,
            )

        # The system prompt depends only on the architecture + agent
        # catalogs (both static at runtime), so cache the rendered string
        # on the instance. Saves ~1–5ms and skips a few hundred string
        # concatenations per compose call.
        system_prompt = self._system_prompt()

        user_msg_parts: list[str] = [f"USER TASK:\n{task_description}"]
        if text_block:
            user_msg_parts.append(f"ATTACHMENTS:\n{text_block}")
        if workspace_context and not workspace_context.is_empty():
            user_msg_parts.append(workspace_context.render())
        if preferred_architecture:
            user_msg_parts.append(
                f"PREFERRED ARCHITECTURE: {preferred_architecture}"
            )
        user_msg_parts.append(
            f"CONTEXT: workspace_id={workspace_id}, "
            f"require_approvals={require_approvals}, "
            f"branch_out={branch_out}"
        )
        user_msg = "\n\n".join(user_msg_parts)

        if image_parts:
            user_content: Any = [{"type": "text", "text": user_msg}, *image_parts]
        else:
            user_content = user_msg

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        # ── Log the outbound LLM call ───────────────────────────
        logger.info(
            "[COMPOSE][%s] ── LLM CALL ──────────────────────────────",
            correlation_id,
        )
        logger.info(
            "[COMPOSE][%s] model=%s | sys_chars=%d | user_text_chars=%d | images=%d",
            correlation_id, chosen_model,
            len(system_prompt), len(user_msg), len(image_parts),
        )
        # Log the task + context the LLM sees (attachment content is
        # already summarised by the per-attachment log lines above —
        # printing it again would flood the logs with PDF/file dumps).
        task_and_context = "\n\n".join(
            p for p in user_msg_parts if not p.startswith("ATTACHMENTS:")
        )
        logger.info(
            "[COMPOSE][%s] user_message (excl. attachment bodies): %.1000s%s",
            correlation_id, task_and_context,
            " [TRUNCATED]" if len(task_and_context) > 1000 else "",
        )

        t0 = time.monotonic()
        raw = await self._call_llm(
            messages=messages, copilot_token=copilot_token,
            correlation_id=correlation_id, model=chosen_model,
        )
        elapsed = time.monotonic() - t0

        # ── Log the raw LLM response ────────────────────────────
        logger.info(
            "[COMPOSE][%s] ── LLM RESPONSE ──────────────────────────",
            correlation_id,
        )
        logger.info(
            "[COMPOSE][%s] responded in %.2fs (%d chars)",
            correlation_id, elapsed, len(raw),
        )
        logger.info(
            "[COMPOSE][%s] raw: %.2000s%s",
            correlation_id, raw,
            " [TRUNCATED]" if len(raw) > 2000 else "",
        )
        try:
            composition = self._parse(
                raw, session_id=session_id, task=task_description,
                require_approvals=require_approvals,
            )
            _log_composition_result(correlation_id, composition)
            return composition
        except CompositionError as e:
            logger.warning(
                "[COMPOSE][%s] ── PARSE FAILED ──────────────────────",
                correlation_id,
            )
            logger.warning(
                "[COMPOSE][%s] reason: %s",
                correlation_id, e.reason,
            )
            logger.warning(
                "[COMPOSE][%s] raw response was: %.500s",
                correlation_id, raw,
            )
            # Single repair retry — tell the model exactly what broke.
            repair_msg = (
                "Your previous response failed schema validation: "
                f"{e.reason}. Emit ONLY a valid JSON Composition."
            )
            logger.info(
                "[COMPOSE][%s] ── RETRY ─────────────────────────────",
                correlation_id,
            )
            logger.info(
                "[COMPOSE][%s] repair prompt: %s",
                correlation_id, repair_msg,
            )
            messages.append({"role": "system", "content": repair_msg})
            raw2 = await self._call_llm(
                messages=messages, copilot_token=copilot_token,
                correlation_id=correlation_id, model=chosen_model,
            )
            logger.info(
                "[COMPOSE][%s] retry raw: %.2000s%s",
                correlation_id, raw2,
                " [TRUNCATED]" if len(raw2) > 2000 else "",
            )
            composition2 = self._parse(
                raw2, session_id=session_id, task=task_description,
                require_approvals=require_approvals,
            )
            _log_composition_result(correlation_id, composition2, retry=True)
            return composition2

    # ── Internals ────────────────────────────────────────────────

    def _system_prompt(self) -> str:
        """Render (and memoize) the composer's system prompt.

        The prompt is a pure function of the three catalogs
        (architectures, agents, recipes) — all static at runtime — so
        we cache the rendered string on the instance and reuse it
        across every compose call.
        """
        cached = getattr(self, "_system_prompt_cache", None)
        if cached is not None:
            return cached
        agents = (
            list_templates()
            if self._agents is AGENT_TEMPLATES
            else list(self._agents.values())
        )
        rendered = (
            build_system_prompt(ARCHITECTURES, agents, RECIPES)
            + "\n\n" + ATTACHMENT_SHIELD_PROMPT
        )
        self._system_prompt_cache = rendered
        return rendered

    async def _call_llm(
        self, *, messages: list[dict], copilot_token: str, correlation_id: str,
        model: str | None = None,
    ) -> str:
        body = {
            "model": model or COMPOSE_MODEL,
            "messages": messages,
            # Low temperature: compose should be near-deterministic
            # given a static catalog + fixed rules. The original 0.3
            # let different LLM families produce materially different
            # team shapes for the same prompt (two FDE slots vs one,
            # Architect included vs not). 0.1 keeps a tiny amount of
            # slack for genuinely ambiguous tasks without inviting
            # creative composition for unambiguous ones.
            "temperature": 0.1,
            "max_tokens": 2_000,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {copilot_token}",
            "Content-Type": "application/json",
            "Copilot-Integration-Id": "vscode-chat",
            "Editor-Version": "vscode/1.100.0",
        }
        resp = await _get_http_client().post(
            f"{COPILOT_API_BASE}/chat/completions",
            json=body, headers=headers,
        )
        if resp.status_code != 200:
            raise CompositionError(
                f"LLM call failed HTTP {resp.status_code}",
                {"body": resp.text[:400]},
            )
        try:
            return resp.json()["choices"][0]["message"]["content"] or ""
        except Exception as exc:  # noqa: BLE001
            raise CompositionError(f"malformed LLM response: {exc}") from exc

    def _parse(
        self,
        raw: str,
        *,
        session_id: str,
        task: str,
        require_approvals: bool,
    ) -> Composition:
        cleaned = _extract_json(raw)
        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise CompositionError(f"not JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise CompositionError("expected JSON object")

        # Architecture validation. Accept a small set of common
        # English synonyms the LLM occasionally emits instead of our
        # canonical ids — cheaper than a retry round-trip.
        arch = str(payload.get("architecture") or "").strip().lower()
        arch = _ARCHITECTURE_ALIASES.get(arch, arch)
        if arch not in ARCHITECTURES_BY_ID:
            raise CompositionError(
                f"unknown architecture '{arch}' — must be one of "
                f"{sorted(ARCHITECTURES_BY_ID)}"
            )
        original_slots = payload.get("slots") or []
        if isinstance(original_slots, list):
            for idx, original_slot in enumerate(original_slots):
                if not isinstance(original_slot, dict):
                    continue
                original_agent_id = str(original_slot.get("agentId") or original_slot.get("agent_id") or "")
                if original_agent_id == "orchestrator":
                    raise CompositionError(
                        f"slot[{idx}] agentId 'orchestrator' is internal control plane and must not be emitted as a user-facing agent"
                    )
        if arch == "dynamic":
            payload["slots"] = [{
                "id": "generalist",
                "agentId": "generalist",
                "role": "Generalist mission controller",
                "skills": [],
            }]
            payload["handoffs"] = []
            payload["entrypointSlotId"] = "generalist"

        # Slot / agent / skill validation. We fix up minor issues the
        # LLM tends to make (missing skill.name, unknown skill id) by
        # dropping the bad field rather than failing the whole compose.
        raw_slots = payload.get("slots") or []
        if not isinstance(raw_slots, list) or not raw_slots:
            raise CompositionError("slots must be a non-empty list")
        slots: list[AgentSlot] = []
        for idx, s in enumerate(raw_slots):
            if not isinstance(s, dict):
                raise CompositionError(f"slot[{idx}] must be an object")
            agent_id = str(s.get("agentId") or s.get("agent_id") or "")
            if agent_id in _INTERNAL_CONTROL_AGENT_IDS:
                raise CompositionError(
                    f"slot[{idx}] agentId '{agent_id}' is internal control plane and must not be emitted as a user-facing agent"
                )
            if agent_id == "generalist":
                known_skills = {}
            elif agent_id not in self._agents:
                raise CompositionError(
                    f"slot[{idx}] agentId '{agent_id}' is not a known agent"
                )
            else:
                tpl = self._agents[agent_id]
                known_skills = {sk.id: sk for sk in tpl.skills}
            raw_skills = s.get("skills") or []
            resolved_skills: list[SkillRef] = []
            for sk in raw_skills:
                if not isinstance(sk, dict):
                    continue
                sk_id = str(sk.get("id") or "")
                tpl_sk = known_skills.get(sk_id)
                if tpl_sk is None:
                    # Drop unknown skill refs rather than fail.
                    logger.info(
                        "compose dropping unknown skill '%s' on agent %s",
                        sk_id, agent_id,
                    )
                    continue
                resolved_skills.append(SkillRef(id=tpl_sk.id, name=tpl_sk.name))
            slots.append(AgentSlot(
                id=str(s.get("id") or f"slot-{idx + 1}"),
                agent_id=agent_id,
                role=str(s.get("role") or agent_id)[:160],
                skills=resolved_skills,
                parent_id=s.get("parentId") or s.get("parent_id"),
                subteam=s.get("subteam"),
            ))

        # Handoffs (optional — sequential/supervisor may emit none and
        # the driver fills them in from slot order).
        raw_handoffs = payload.get("handoffs") or []
        handoffs: list[Handoff] = []
        slot_ids = {s.id for s in slots}
        for h in raw_handoffs:
            if not isinstance(h, dict):
                continue
            frm = str(h.get("from") or "")
            to = str(h.get("to") or "")
            if frm not in slot_ids or to not in slot_ids:
                logger.info(
                    "compose dropping handoff with unknown slot ids: %s → %s",
                    frm, to,
                )
                continue
            handoffs.append(Handoff.model_validate({
                "from": frm, "to": to,
                "kind": h.get("kind") or "delegate",
                "condition": h.get("condition"),
            }))

        entrypoint = str(payload.get("entrypointSlotId") or payload.get("entrypoint_slot_id") or "")
        if entrypoint not in slot_ids:
            entrypoint = slots[0].id

        budget_raw = payload.get("budget") or {}
        if not isinstance(budget_raw, dict):
            budget_raw = {}
        budget = Budget(
            max_turns=int(budget_raw.get("maxTurns") or budget_raw.get("max_turns") or 20),
            max_tool_calls=int(budget_raw.get("maxToolCalls") or budget_raw.get("max_tool_calls") or 100),
            max_wallclock_s=int(budget_raw.get("maxWallclockS") or budget_raw.get("max_wallclock_s") or 600),
            require_approvals=bool(
                budget_raw.get("requireApprovals")
                if budget_raw.get("requireApprovals") is not None
                else budget_raw.get("require_approvals", require_approvals)
            ),
        )

        headline = str(payload.get("headline") or "").strip()
        subtitle = str(payload.get("subtitle") or "").strip()
        if not headline:
            entry = ARCHITECTURES_BY_ID[arch]
            headline = entry.headline
        if not subtitle:
            subtitle = ARCHITECTURES_BY_ID[arch].description[:200]

        rationale = str(payload.get("rationale") or "Composition derived from the task prompt.").strip()

        # Post-parse normalisation: enforce sequential uniqueness even
        # when the LLM ignores rule 4 in the global rules. For
        # sequential architectures, emitting two slots with the same
        # ``agentId`` produces two disjoint agent loops with no shared
        # state — almost always wrong. We merge duplicates into a
        # single slot (first one wins, roles are concatenated) and
        # rewrite handoffs to match. Do NOT apply this to supervisor:
        # fan-out patterns may legitimately use the same agent template
        # in multiple lead/worker roles.
        if arch == "sequential":
            slots, handoffs, entrypoint = _collapse_duplicate_agent_slots(
                slots, handoffs, entrypoint,
            )

        arch, slots, handoffs, entrypoint = _enforce_quality_gate_defaults(
            arch, slots, handoffs, entrypoint, task, self._agents,
        )

        _validate_architecture_shape(arch, slots, handoffs)

        return Composition(
            session_id=session_id,
            task=task[:16_000],
            architecture=arch,  # type: ignore[arg-type]
            rationale=rationale[:1_200],
            headline=headline[:200],
            subtitle=subtitle[:400],
            slots=slots,
            handoffs=handoffs,
            entrypoint_slot_id=entrypoint,
            budget=budget,
        )


_service_singleton: ComposeService | None = None


def get_compose_service() -> ComposeService:
    global _service_singleton
    if _service_singleton is None:
        _service_singleton = ComposeService()
    return _service_singleton


def _collapse_duplicate_agent_slots(
    slots: list[AgentSlot],
    handoffs: list[Handoff],
    entrypoint: str,
) -> tuple[list[AgentSlot], list[Handoff], str]:
    """Merge slots that share the same ``agent_id``.

    Sequential plans should not instantiate the same agent twice: it
    produces disjoint agent loops with no shared memory, which almost
    always means the composer mis-decomposed the task (e.g. two
    ``FabricDataEngineer`` slots for "ingestion" and "transformation",
    when one slot covers the whole build phase). Do not apply this to
    supervisor fan-out plans; repeated agent templates can be valid
    when the lead/worker roles are distinct.

    Strategy: keep the first occurrence, concatenate its ``role`` with
    the duplicates' roles so context isn't lost, then rewrite every
    handoff that referenced the dropped slot ids to the kept one.
    Self-loop handoffs (from=to) created by the rewrite are removed.

    No-op when all agent_ids are already unique.
    """
    if not slots:
        return slots, handoffs, entrypoint
    seen: dict[str, AgentSlot] = {}
    id_remap: dict[str, str] = {}
    kept: list[AgentSlot] = []
    for s in slots:
        first = seen.get(s.agent_id)
        if first is None:
            seen[s.agent_id] = s
            kept.append(s)
            id_remap[s.id] = s.id
            continue
        # Fold this slot into the first one with the same agent.
        id_remap[s.id] = first.id
        dup_role = (s.role or "").strip()
        if dup_role and dup_role not in first.role:
            combined = f"{first.role}; {dup_role}" if first.role else dup_role
            first.role = combined[:160]
        # Merge skills (dedup by id) so we don't lose skill hints.
        known = {sk.id for sk in first.skills}
        for sk in s.skills:
            if sk.id not in known:
                first.skills.append(sk)
                known.add(sk.id)
    if len(kept) == len(slots):
        return slots, handoffs, entrypoint
    # Rewrite handoffs.
    new_handoffs: list[Handoff] = []
    seen_pairs: set[tuple[str, str, str]] = set()
    for h in handoffs:
        frm = id_remap.get(h.from_, h.from_)
        to = id_remap.get(h.to, h.to)
        if frm == to:
            continue  # self-loop after merge — drop
        key = (frm, to, h.kind)
        if key in seen_pairs:
            continue  # dedup
        seen_pairs.add(key)
        new_handoffs.append(Handoff.model_validate({
            "from": frm, "to": to, "kind": h.kind, "condition": h.condition,
        }))
    new_entry = id_remap.get(entrypoint, entrypoint)
    if new_entry not in {s.id for s in kept}:
        new_entry = kept[0].id
    logger.info(
        "compose merged %d duplicate agent slot(s): %s",
        len(slots) - len(kept),
        [s for s, k in id_remap.items() if s != k],
    )
    return kept, new_handoffs, new_entry


_CREATE_OR_MODIFY_RE = _re.compile(
    r"\b(create|build|modify|update|fix|optimi[sz]e|publish|generate|write|implement|deploy|produce|refine|polish)\b",
    _re.IGNORECASE,
)
_READ_ONLY_RE = _re.compile(
    r"\b(read[-\s]?only|list|inspect|inventory|summari[sz]e|describe|show|check)\b",
    _re.IGNORECASE,
)
_NO_REVIEW_RE = _re.compile(
    r"\b(?:"
    r"(?:no|without|do not)\s+(?:review|critic|critique|verify|validate|verifier|quality\s+gate|"
    r"delegate|branch|supervise|debate|add\s+(?:any\s+)?(?:extra|additional)\s+agents?)"
    r"|single[-\s]?agent|one[-\s]?agent|exactly\s+one\s+(?:agent|slot)|no\s+extra\s+agents?"
    r")\b",
    _re.IGNORECASE,
)
_ARTIFACT_RE = _re.compile(
    r"\b(report|dashboard|semantic\s+model|model|notebook|pipeline|dataflow|query|kql|sql|dax|measure|lakehouse|warehouse|eventhouse|config|code|artifact|page|visual)\b",
    _re.IGNORECASE,
)
_VISUAL_DELIVERABLE_RE = _re.compile(
    r"\b(report|dashboard|visual|layout|presentation|leadership|executive|power\s*bi|variance|ibcs|design|appealing|polish(?:ed)?)\b",
    _re.IGNORECASE,
)
_VERIFIABLE_RE = _re.compile(
    r"\b(test|verify|validate|execute|run|render|publish|query|notebook|pipeline|report|semantic\s+model|dax|sql|kql)\b",
    _re.IGNORECASE,
)
_MULTI_DOMAIN_RE = _re.compile(
    r"\b(ingest|transform|curat(?:e|ed)|semantic\s+model|report|dashboard|pipeline|lakehouse|warehouse|governance|rbac|workspace|app|api)\b",
    _re.IGNORECASE,
)
_QUALITY_ROLE_RE = _re.compile(
    r"\b(critic|critique|review|reviewer|quality|designer|visual|presentation|verifier|verify|validator|approver)\b",
    _re.IGNORECASE,
)


def _enforce_quality_gate_defaults(
    arch: str,
    slots: list[AgentSlot],
    handoffs: list[Handoff],
    entrypoint: str,
    task: str,
    agents: dict[str, AgentTemplate],
) -> tuple[str, list[AgentSlot], list[Handoff], str]:
    """Normalize under-composed artifact work into quality-gated teams.

    The system prompt already tells the LLM that users should not need
    to say "iterate", "review", or "verify" to get a critic/verifier
    loop. This guardrail makes that contract durable when a model emits
    a too-simple shape anyway:

    * single artifact create/modify -> ``reflection``
    * multi-domain user-facing deliverable -> ``mixed`` with a quality
      subteam

    Explicit read-only tasks and explicit "no review / no verification"
    requests are preserved.
    """
    if arch == "dynamic":
        return arch, slots, handoffs, entrypoint
    if not slots or not _task_needs_quality_gate(task):
        return arch, slots, handoffs, entrypoint

    visual = bool(_VISUAL_DELIVERABLE_RE.search(task))
    verifiable = bool(_VERIFIABLE_RE.search(task))
    multi_domain = _is_multi_domain_deliverable(task)
    has_quality_slot = any(_slot_is_quality_gate(s) for s in slots)

    if arch == "solo":
        actor = slots[0]
        critic = _make_quality_slot(
            "critic",
            agents,
            visual=visual,
            role=(
                "Critic: review the artifact for correctness, completeness, "
                "visual clarity, and presentation readiness before delivery."
                if visual else
                "Critic: review the artifact for correctness and completeness before delivery."
            ),
            fallback_agent_id=actor.agent_id,
        )
        new_slots = [actor, critic]
        new_handoffs = [
            _handoff(actor.id, critic.id, "critique"),
            _handoff(critic.id, actor.id, "report"),
        ]
        if verifiable:
            verifier_slot = _make_quality_slot(
                "verifier",
                agents,
                visual=visual,
                verifier=True,
                role=(
                    "Verifier: validate the final artifact against the original task, "
                    "confirm required outputs exist, inspect live data/results, and "
                    "approve only when the result is ready. If validation fails, "
                    "return concrete repair requirements."
                ),
                fallback_agent_id=critic.agent_id,
            )
            new_slots.append(verifier_slot)
            new_handoffs.append(_handoff(actor.id, verifier_slot.id, "verify"))
        logger.info("compose upgraded solo artifact task to reflection quality gate")
        return "reflection", new_slots, new_handoffs, actor.id

    if multi_domain and arch not in {"mixed", "reflection"}:
        arch = "mixed"

    if arch == "mixed" and not has_quality_slot:
        last = slots[-1]
        reviewer = _make_quality_slot(
            _unique_slot_id(slots, "quality-review"),
            agents,
            visual=visual,
            role=(
                "Quality reviewer / designer: critique the final deliverable, "
                "judge visual appeal and metric correctness, and require "
                "revision until it is presentation-ready."
                if visual else
                "Quality reviewer: critique the final deliverable and require revision until it is ready."
            ),
            fallback_agent_id=last.agent_id,
        )
        slots = [*slots, reviewer]
        handoffs = [*handoffs, _handoff(last.id, reviewer.id, "critique")]
        if verifiable:
            verifier_slot = _make_quality_slot(
                _unique_slot_id(slots, "final-verifier"),
                agents,
                visual=visual,
                verifier=True,
                role=(
                    "Final verifier: validate requested outputs against the original task, "
                    "inspect live Fabric items/data/visuals, prove they are ready for users, "
                    "and return actionable repair requirements for any mismatch."
                ),
                fallback_agent_id=reviewer.agent_id,
            )
            slots = [*slots, verifier_slot]
            handoffs = [*handoffs, _handoff(reviewer.id, verifier_slot.id, "verify")]
        logger.info("compose added mixed quality gate for multi-domain deliverable")

    return arch, slots, handoffs, entrypoint


def _task_needs_quality_gate(task: str) -> bool:
    text = task or ""
    if _NO_REVIEW_RE.search(text):
        return False
    if _READ_ONLY_RE.search(text) and not _CREATE_OR_MODIFY_RE.search(text):
        return False
    return bool(_CREATE_OR_MODIFY_RE.search(text) and _ARTIFACT_RE.search(text))


def _is_multi_domain_deliverable(task: str) -> bool:
    hits = {m.group(0).lower() for m in _MULTI_DOMAIN_RE.finditer(task or "")}
    return len(hits) >= 3 and bool(_VISUAL_DELIVERABLE_RE.search(task or ""))


def _slot_is_quality_gate(slot: AgentSlot) -> bool:
    text = " ".join([slot.id, slot.role, *(sk.id for sk in slot.skills), *(sk.name for sk in slot.skills)])
    return bool(_QUALITY_ROLE_RE.search(text))


def _make_quality_slot(
    slot_id: str,
    agents: dict[str, AgentTemplate],
    *,
    visual: bool,
    role: str,
    verifier: bool = False,
    fallback_agent_id: str,
) -> AgentSlot:
    if verifier and "fabric-verifier" in agents:
        agent_id = "fabric-verifier"
    else:
        agent_id = "modeler" if visual and "modeler" in agents else fallback_agent_id
    skills: list[SkillRef] = []
    if agent_id in agents:
        available = {sk.id: sk for sk in agents[agent_id].skills}
        if visual and not verifier and "powerbi-ibcs" in available:
            sk = available["powerbi-ibcs"]
            skills.append(SkillRef(id=sk.id, name=sk.name))
        if "fabric-verification" in available:
            sk = available["fabric-verification"]
            if all(existing.id != sk.id for existing in skills):
                skills.append(SkillRef(id=sk.id, name=sk.name))
    return AgentSlot(id=slot_id, agent_id=agent_id, role=role[:160], skills=skills)


def _handoff(frm: str, to: str, kind: str) -> Handoff:
    return Handoff.model_validate({"from": frm, "to": to, "kind": kind, "condition": None})


def _unique_slot_id(slots: list[AgentSlot], base: str) -> str:
    existing = {s.id for s in slots}
    if base not in existing:
        return base
    i = 2
    while f"{base}-{i}" in existing:
        i += 1
    return f"{base}-{i}"


def _validate_architecture_shape(
    arch: str,
    slots: list[AgentSlot],
    handoffs: list[Handoff],
) -> None:
    """Reject structurally impossible compositions before runtime.

    The most important invariant: ``solo`` is the only architecture
    allowed to have a single slot. Every other topology is, by
    definition, multi-agent. If the LLM emits ``architecture`` =
    supervisor/sequential/etc. with one slot, the parser must fail so
    the compose retry can repair it instead of the E2E test silently
    running a solo-shaped plan under a multi-agent label.
    """
    slot_count = len(slots)
    if arch == "dynamic":
        if slot_count != 1:
            raise CompositionError(
                f"dynamic architecture requires exactly 1 generalist slot; got {slot_count}"
            )
        slot = slots[0]
        if slot.id != "generalist" or slot.agent_id != "generalist":
            raise CompositionError("dynamic architecture must start with the internal generalist slot")
        if handoffs:
            raise CompositionError("dynamic architecture must not include upfront handoffs")
        return
    if arch == "solo":
        if slot_count != 1:
            raise CompositionError(
                f"solo architecture requires exactly 1 slot; got {slot_count}"
            )
        if handoffs:
            raise CompositionError("solo architecture must not include handoffs")
        return

    if slot_count < 2:
        if arch == "sequential":
            raise CompositionError(
                "sequential architecture requires at least 2 distinct "
                "agentIds; only solo may use one slot"
            )
        raise CompositionError(
            f"{arch} architecture requires at least 2 slots; only solo may use one slot"
        )

    if arch == "reflection" and slot_count not in (2, 3):
        raise CompositionError(
            f"reflection architecture requires 2 or 3 slots; got {slot_count}"
        )
    if arch == "hierarchical" and slot_count < 3:
        raise CompositionError(
            f"hierarchical architecture requires at least 3 slots; got {slot_count}"
        )
    if arch == "mixed" and slot_count < 3:
        raise CompositionError(
            f"mixed architecture requires at least 3 slots; got {slot_count}"
        )
