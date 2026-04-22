"""Capability registry — single source of truth for *capabilities*.

This is Phase 1 of the redesign described in the architecture notes. The
catalog data still lives in ``agent_registry.py`` (Python literals) today;
this module wraps it in a typed API and adds two things the old shape
lacked:

1. **Startup validation** — crosscheck every tool a skill claims to use
   against the tools actually discovered by ``MCPClientManager``, and
   every skill an agent references against the skill catalog. Drift
   shows up as a ``CapabilityIssue`` at boot instead of a silent
   "tool not found" at runtime.
2. **Qualified tool IDs** — ``server_id::tool_name`` rendering so logs
   and error messages are unambiguous even when two servers (one day)
   expose tools with the same short name.

Nothing in the existing dispatch path is rewired yet — ``call_tool``
still keys by the bare tool name, and the LLM still sees bare names in
the OpenAI schema. Internal callers can now ask the registry for the
qualified name whenever they log or raise.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain.models.skill import Skill

if TYPE_CHECKING:
    from services.mcp.mcp_client_manager import MCPClientManager

logger = logging.getLogger(__name__)


class IssueSeverity:
    """String enum for ``CapabilityIssue.severity``.

    Not a ``StrEnum`` so consumers can pattern-match on the bare string
    without importing this module.
    """

    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class CapabilityIssue:
    """A drift finding between the declared catalog and runtime state.

    Attributes:
        severity: ``"error"`` for impossible-at-runtime links (skill →
            unknown tool, agent → unknown skill); ``"warning"`` for
            smells that don't block dispatch (e.g. skill listed on no
            agent).
        kind: machine-readable issue category, stable across versions.
        subject_id: the id being checked (skill id, agent id, or tool
            name), never free-text.
        detail: human-readable explanation. Safe to log verbatim.
    """

    severity: str
    kind: str
    subject_id: str
    detail: str

    def format(self) -> str:
        return f"[{self.severity.upper()}] {self.kind} {self.subject_id!r}: {self.detail}"


def qualified_tool_id(
    tool_name: str,
    mcp_manager: MCPClientManager | None,
) -> str:
    """Render ``server_id::tool_name`` for a known tool, or a helpful
    fallback string when the manager hasn't discovered it.

    Used in log lines and exception messages so operators can tell at a
    glance which server a tool came from.
    """
    if mcp_manager is None:
        return f"<unknown-server>::{tool_name}"
    server = mcp_manager.tool_server_map.get(tool_name)
    if server is None:
        return f"<undiscovered>::{tool_name}"
    return f"{server}::{tool_name}"


def validate_skill_references(
    skills: dict[str, Skill],
    discovered_tool_names: Iterable[str],
    unavailable_servers: dict[str, str] | None = None,
) -> list[CapabilityIssue]:
    """Check every skill's ``tools`` list against the discovered set.

    Emits at most **one issue per skill**, aggregating all missing tool
    names into a single ``detail`` string. This keeps startup logs
    readable when a whole server is down (e.g. ``npx`` missing in the
    container takes out all ``docs_*`` tools — that is one operator
    problem, not six catalog bugs).

    Severity:

    * ``ERROR`` when the deployment reports no unavailable servers —
      the missing tool is almost certainly a typo in ``catalog.yaml``.
    * ``WARNING`` when at least one MCP server is known to be
      pruned or failed. Some or all of the missing tools may come
      from those servers, and this is an ops issue rather than a
      catalog bug. The detail text includes the unavailable server
      list so operators know where to look.

    Skills with an empty ``tools`` list are allowed (informational-only
    skills like ``check-updates`` exist by design).
    """
    known = set(discovered_tool_names)
    unavailable = unavailable_servers or {}
    issues: list[CapabilityIssue] = []
    for skill_id, skill in skills.items():
        missing = [tool for tool in skill.tools if tool not in known]
        if not missing:
            continue
        if unavailable:
            severity = IssueSeverity.WARNING
            server_list = ", ".join(sorted(unavailable))
            detail = (
                f"{len(missing)} tool(s) not provided by any running MCP "
                f"server: {missing}. Unavailable servers this deploy: "
                f"{server_list}. Skill is unreachable until those "
                f"servers start (or the reference is removed)."
            )
        else:
            severity = IssueSeverity.ERROR
            detail = (
                f"{len(missing)} tool(s) not provided by any running MCP "
                f"server: {missing}. The compose LLM may pick this skill "
                f"and the runtime will then fail with 'Unknown tool'."
            )
        issues.append(
            CapabilityIssue(
                severity=severity,
                kind="skill_tool_missing",
                subject_id=skill_id,
                detail=detail,
            )
        )
    return issues


def validate_agent_skill_references(
    agent_skills: dict[str, list[str]],
    known_skill_ids: Iterable[str],
) -> list[CapabilityIssue]:
    """Check every agent's declared skill list against the skill catalog."""
    known = set(known_skill_ids)
    issues: list[CapabilityIssue] = []
    for agent_id, skill_ids in agent_skills.items():
        for skill_id in skill_ids:
            if skill_id not in known:
                issues.append(
                    CapabilityIssue(
                        severity=IssueSeverity.ERROR,
                        kind="agent_skill_missing",
                        subject_id=agent_id,
                        detail=(
                            f"agent declares skill {skill_id!r} which does "
                            f"not exist in the skill catalog."
                        ),
                    )
                )
    return issues


def validate_catalog(
    skills: dict[str, Skill],
    agent_skills: dict[str, list[str]],
    mcp_manager: MCPClientManager | None,
) -> list[CapabilityIssue]:
    """Run the full cross-catalog validation and return all issues.

    When ``mcp_manager`` is ``None`` (discovery failed, tests, etc.)
    tool-level checks are skipped and only the skill-reference graph is
    validated. This keeps the function useful in offline test runs
    without requiring a live MCP stack.

    If the manager exposes ``unavailable_servers()`` (pruned +
    failed-to-discover), that dict is passed through so
    ``validate_skill_references`` can classify missing tools as an ops
    issue (WARNING) rather than a catalog bug (ERROR).
    """
    issues: list[CapabilityIssue] = []
    issues.extend(validate_agent_skill_references(agent_skills, skills.keys()))
    if mcp_manager is not None:
        unavailable = (
            mcp_manager.unavailable_servers()
            if hasattr(mcp_manager, "unavailable_servers")
            else {}
        )
        if unavailable:
            logger.info(
                "[CAPABILITY] MCP servers unavailable this deploy: %s. "
                "Skills that depend on their tools will be flagged below "
                "as WARNING rather than ERROR.",
                ", ".join(f"{name} ({reason})" for name, reason in sorted(unavailable.items())),
            )
        issues.extend(
            validate_skill_references(
                skills,
                mcp_manager.tools.keys(),
                unavailable_servers=unavailable,
            )
        )
    return issues


def log_issues(issues: list[CapabilityIssue]) -> None:
    """Log each issue at its declared severity.

    Errors are logged but do not raise — a single dangling reference
    shouldn't take the whole backend down; it should surface in startup
    output and healthchecks so it gets fixed in the next deploy.
    """
    for issue in issues:
        if issue.severity == IssueSeverity.ERROR:
            logger.error("[CAPABILITY] %s", issue.format())
        else:
            logger.warning("[CAPABILITY] %s", issue.format())
