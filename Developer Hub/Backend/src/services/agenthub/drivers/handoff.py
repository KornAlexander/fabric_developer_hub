"""HandoffPayload model and HandoffExtractor."""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from domain.models.agent_models import AgentAssignment, AgentStatus

logger = logging.getLogger(__name__)

HandoffKind = Literal["delegate", "report", "peer", "handoff", "critique", "verify"]
_VALID_HANDOFF_KINDS = {"delegate", "report", "peer", "handoff", "critique", "verify"}


class HandoffPayload(BaseModel):
    """Structured transfer between slots."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    from_slot_id: str = Field(max_length=64)
    to_slot_id: str = Field(max_length=64)
    kind: HandoffKind
    status: Literal["success", "partial", "error"]
    summary: str = Field(max_length=2_000)
    artifacts: list[dict] = Field(default_factory=list)
    key_outputs: dict[str, str] = Field(default_factory=dict)
    error: str | None = Field(default=None, max_length=500)

    def render_for_injection(self, role: str) -> str:
        """Serialize for injection into a downstream agent's goal."""
        lines = [
            f"[UPSTREAM HANDOFF from {role} ({self.from_slot_id})]",
            f"Status: {self.status}",
            f"Summary: {self.summary}",
        ]
        if self.artifacts:
            arts = ", ".join(
                (
                    f"{a.get('name', '?')} ({a.get('kind', '?')}, id={a.get('id')})"
                    if a.get("id")
                    else f"{a.get('name', '?')} ({a.get('kind', '?')})"
                )
                for a in self.artifacts[:10]
            )
            lines.append(f"Artifacts: {arts}")
        if self.key_outputs:
            for k, v in list(self.key_outputs.items())[:5]:
                lines.append(f"  {k}: {v[:200]}")
        if self.error:
            lines.append(f"Error: {self.error}")
        lines.append("[END UPSTREAM HANDOFF]")
        return "\n".join(lines)


def _truncate_summary(text: str, max_len: int = 2000) -> str:
    if len(text) <= max_len:
        return text
    half = (max_len - 30) // 2
    return text[:half] + "\n[...truncated...]\n" + text[-half:]


class HandoffExtractor:
    """Extracts a HandoffPayload from a completed agent's state."""

    def extract(
        self,
        slot_id: str,
        target_slot_id: str,
        kind: str,
        assignment: AgentAssignment,
    ) -> HandoffPayload:
        normalized_kind = _normalize_handoff_kind(kind)
        # Summary: last decision text, else last phase details
        summary = ""
        if assignment.phases:
            last_phase = assignment.phases[-1]
            if last_phase.decisions:
                summary = last_phase.decisions[-1].summary
            elif last_phase.details:
                summary = "\n".join(last_phase.details)
        if not summary:
            summary = assignment.current_step or f"Agent {assignment.agent_id} completed"
        summary = _truncate_summary(summary)

        # Artifacts
        artifacts = []
        for a in assignment.actions[-10:]:
            entry = {"type": a.action_type, "name": a.entity_name, "kind": a.entity_type}
            if a.fabric_item_id:
                entry["id"] = a.fabric_item_id
            artifacts.append(entry)

        key_outputs: dict[str, str] = {}
        for artifact in artifacts:
            artifact_id = artifact.get("id")
            kind = str(artifact.get("kind") or "").lower()
            if not artifact_id or not kind:
                continue
            key_outputs.setdefault(f"{kind}_id", str(artifact_id))
            if artifact.get("name"):
                key_outputs.setdefault(f"{kind}_name", str(artifact["name"]))

        # Status
        if assignment.status == AgentStatus.COMPLETED:
            has_decision = any(
                d for p in assignment.phases for d in p.decisions
            )
            status: Literal["success", "partial", "error"] = "success" if has_decision else "partial"
        elif assignment.status == AgentStatus.ERROR:
            status = "error"
        else:
            status = "partial"

        error = None
        if status == "error":
            error = (assignment.current_step or "Unknown error")[:500]

        return HandoffPayload(
            from_slot_id=slot_id,
            to_slot_id=target_slot_id,
            kind=normalized_kind,
            status=status,
            summary=summary,
            artifacts=artifacts,
            key_outputs=key_outputs,
            error=error,
        )


def _normalize_handoff_kind(kind: str) -> HandoffKind:
    if kind in _VALID_HANDOFF_KINDS:
        return kind  # type: ignore[return-value]
    logger.warning("Invalid handoff kind %r; using 'handoff'", kind)
    return "handoff"
