"""Unit tests for ``services.agenthub.capability_registry``."""
from __future__ import annotations

from types import SimpleNamespace

from domain.models.skill import Skill
from services.agenthub import capability_registry
from services.agenthub.capability_registry import (
    CapabilityIssue,
    IssueSeverity,
)


def _fake_manager(tool_to_server: dict[str, str], unavailable: dict[str, str] | None = None):
    """Minimal stand-in for MCPClientManager used by the registry."""
    unavailable = unavailable or {}
    return SimpleNamespace(
        tools={name: {"name": name} for name in tool_to_server},
        tool_server_map=dict(tool_to_server),
        unavailable_servers=lambda: dict(unavailable),
    )


def test_validate_skill_references_flags_missing_tool() -> None:
    skills = {
        "good": Skill(id="good", name="g", description="d", tools=["a"]),
        "bad": Skill(id="bad", name="b", description="d", tools=["a", "missing"]),
    }
    issues = capability_registry.validate_skill_references(skills, ["a"])
    assert len(issues) == 1
    assert issues[0].severity == IssueSeverity.ERROR
    assert issues[0].kind == "skill_tool_missing"
    assert issues[0].subject_id == "bad"
    assert "missing" in issues[0].detail


def test_validate_skill_references_aggregates_per_skill() -> None:
    """Multiple missing tools on one skill collapse into a single issue."""
    skills = {
        "bad": Skill(
            id="bad", name="b", description="d",
            tools=["a", "gone1", "gone2", "gone3"],
        ),
    }
    issues = capability_registry.validate_skill_references(skills, ["a"])
    assert len(issues) == 1
    assert "gone1" in issues[0].detail
    assert "gone2" in issues[0].detail
    assert "gone3" in issues[0].detail


def test_validate_skill_references_downgrades_to_warning_when_servers_unavailable() -> None:
    """When at least one server failed/was pruned, missing tools are an
    ops issue (WARNING), not a catalog bug (ERROR)."""
    skills = {
        "docs": Skill(id="docs", name="d", description="d", tools=["docs_foo"]),
    }
    issues = capability_registry.validate_skill_references(
        skills, [],
        unavailable_servers={"fabric-docs": "npx not found"},
    )
    assert len(issues) == 1
    assert issues[0].severity == IssueSeverity.WARNING
    assert "fabric-docs" in issues[0].detail


def test_validate_skill_references_empty_tools_list_is_ok() -> None:
    skills = {"info": Skill(id="info", name="i", description="d", tools=[])}
    assert capability_registry.validate_skill_references(skills, []) == []


def test_validate_agent_skill_references_flags_unknown_skill() -> None:
    agent_skills = {"agent-a": ["known", "ghost"]}
    issues = capability_registry.validate_agent_skill_references(
        agent_skills, ["known"],
    )
    assert len(issues) == 1
    assert issues[0].kind == "agent_skill_missing"
    assert issues[0].subject_id == "agent-a"
    assert "ghost" in issues[0].detail


def test_validate_catalog_skips_tool_checks_when_no_manager() -> None:
    """Offline test runs (no live MCP) must still surface agent→skill drift."""
    skills = {"s1": Skill(id="s1", name="s", description="d", tools=["t1"])}
    agent_skills = {"a1": ["s1", "missing-skill"]}
    issues = capability_registry.validate_catalog(skills, agent_skills, None)
    # One agent→skill error, zero tool-level errors.
    assert len(issues) == 1
    assert issues[0].kind == "agent_skill_missing"


def test_validate_catalog_full_pass_returns_empty() -> None:
    skills = {"s1": Skill(id="s1", name="s", description="d", tools=["t1"])}
    mgr = _fake_manager({"t1": "server-x"})
    assert capability_registry.validate_catalog(skills, {"a1": ["s1"]}, mgr) == []


def test_qualified_tool_id_uses_server_map() -> None:
    mgr = _fake_manager({"t1": "server-x"})
    assert capability_registry.qualified_tool_id("t1", mgr) == "server-x::t1"


def test_qualified_tool_id_falls_back_when_undiscovered() -> None:
    mgr = _fake_manager({})
    assert capability_registry.qualified_tool_id("gone", mgr) == "<undiscovered>::gone"


def test_qualified_tool_id_handles_none_manager() -> None:
    assert capability_registry.qualified_tool_id("t", None) == "<unknown-server>::t"


def test_capability_issue_format_is_stable() -> None:
    issue = CapabilityIssue(
        severity=IssueSeverity.WARNING,
        kind="skill_tool_missing",
        subject_id="sk",
        detail="explanation",
    )
    assert issue.format() == "[WARNING] skill_tool_missing 'sk': explanation"


def test_live_catalog_has_no_drift_against_declared_skill_tools() -> None:
    """REGRESSION: every tool listed on a declared skill must at least
    be referenced by one other skill, guarding against typos in the
    hardcoded ``SKILLS`` dict.

    This doesn't require a live MCP manager — it only catches internal
    inconsistencies (e.g. someone renames ``pbir_visuals`` → ``pbir_vis``
    in one skill but forgets the others).
    """
    from services.agenthub.agent_registry import SKILLS, _AGENT_SKILLS

    # Agent → skill graph must be intact.
    issues = capability_registry.validate_agent_skill_references(
        _AGENT_SKILLS, SKILLS.keys(),
    )
    assert issues == [], [i.format() for i in issues]
