"""Unit tests for ``services.agenthub.catalog_loader``."""
from __future__ import annotations

from pathlib import Path

import pytest

from services.agenthub.catalog_loader import (
    CatalogLoadError,
    load_catalog,
)


def test_default_catalog_loads_and_matches_known_shape() -> None:
    """REGRESSION: the bundled catalog.yaml must load cleanly and
    contain the 17 skills and 7 public agents the redesign is built around.

    Guards against accidental deletions / typos in the YAML file that
    would only surface at boot time otherwise.
    """
    skills, agent_skills = load_catalog()

    # Spot-check the count so future additions don't slip through
    # unnoticed — bump this number when you deliberately add a skill.
    # Current: 10 from skills-for-fabric + 3 unique from
    # AnalyticsPlatformAgents + 1 Fabric Local MCP docs grounding
    # + 1 team-orchestration (coordinator-plane) + 1 verification
    # super skill + 1 Fabric Remote Core MCP skill = 17.
    assert len(skills) == 17
    assert len(agent_skills) == 7

    expected_agents = {
        "fabric-admin", "fabric-app-dev", "fabric-data-engineer",
        "architect", "modeler", "creator", "fabric-verifier",
    }
    assert set(agent_skills.keys()) == expected_agents

    # The Fabric-docs grounding skill and its attachment to the four
    # code-generating agents must persist through YAML migration.
    assert "fabric-api-docs" in skills
    for agent in ("fabric-app-dev", "fabric-data-engineer", "architect", "modeler"):
        assert "fabric-api-docs" in agent_skills[agent]

    assert "fabric-remote-core" in skills
    assert "list_workspaces" in skills["fabric-remote-core"].tools
    assert "get_item_definition" in skills["fabric-remote-core"].tools
    for agent in (
        "fabric-admin", "fabric-app-dev", "fabric-data-engineer",
        "architect", "modeler", "fabric-verifier",
    ):
        assert "fabric-remote-core" in agent_skills[agent]

    assert "team-orchestration" in skills
    # team-orchestration is intentionally tool-less (control plane).
    assert skills["team-orchestration"].tools == []
    assert "fabric-verification" in skills
    assert "fabric_verify_workspace_inventory_solution" in skills["fabric-verification"].tools
    assert "browser_verify_visual_render" in skills["fabric-verification"].tools
    assert agent_skills["fabric-verifier"][0] == "fabric-verification"


def test_every_skill_agent_reference_resolves() -> None:
    """Cross-reference check: every skill-id under ``agent_skills`` must
    exist in ``skills``. Cheap to run, catches typos the pydantic schema
    cannot.
    """
    skills, agent_skills = load_catalog()
    known = set(skills.keys())
    for agent_id, skill_ids in agent_skills.items():
        for sid in skill_ids:
            assert sid in known, f"agent {agent_id!r} references unknown skill {sid!r}"


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(CatalogLoadError, match="not found"):
        load_catalog(tmp_path / "no-such-file.yaml")


def test_malformed_yaml_raises(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text(":\n  not: valid: yaml: here\n   indent", encoding="utf-8")
    with pytest.raises(CatalogLoadError, match="failed to parse"):
        load_catalog(path)


def test_missing_top_level_skills_key_raises(tmp_path: Path) -> None:
    path = tmp_path / "partial.yaml"
    path.write_text("agent_skills:\n  a1: []\n", encoding="utf-8")
    with pytest.raises(CatalogLoadError, match="'skills'"):
        load_catalog(path)


def test_missing_top_level_agents_key_raises(tmp_path: Path) -> None:
    path = tmp_path / "partial.yaml"
    path.write_text(
        "skills:\n"
        "  s1:\n"
        "    name: s\n"
        "    description: d\n"
        "    tools: []\n",
        encoding="utf-8",
    )
    with pytest.raises(CatalogLoadError, match="'agent_skills'"):
        load_catalog(path)


def test_skill_missing_required_field_raises(tmp_path: Path) -> None:
    path = tmp_path / "broken-skill.yaml"
    path.write_text(
        "skills:\n"
        "  s1:\n"
        "    name: s\n"  # no description
        "    tools: []\n"
        "agent_skills: {}\n",
        encoding="utf-8",
    )
    with pytest.raises(CatalogLoadError, match="description"):
        load_catalog(path)


def test_agent_skill_list_must_contain_strings(tmp_path: Path) -> None:
    path = tmp_path / "bad-agent.yaml"
    path.write_text(
        "skills: {}\n"
        "agent_skills:\n"
        "  a1:\n"
        "    - 123\n",
        encoding="utf-8",
    )
    with pytest.raises(CatalogLoadError, match="a1"):
        load_catalog(path)


def test_description_whitespace_is_collapsed(tmp_path: Path) -> None:
    """Folded-scalar YAML descriptions must be squashed to single-spaced
    text so the Skill 600-char cap and compose-prompt token cost stay
    comparable to the old Python-literal form.
    """
    path = tmp_path / "folded.yaml"
    path.write_text(
        "skills:\n"
        "  s1:\n"
        "    name: s\n"
        "    description: >-\n"
        "      multi\n"
        "      line\n"
        "      text\n"
        "    tools: []\n"
        "agent_skills: {}\n",
        encoding="utf-8",
    )
    skills, _ = load_catalog(path, overlay_path=False)
    assert skills["s1"].description == "multi line text"


# ── Overlay tests ──────────────────────────────────────────────────

def _write_base(tmp_path: Path) -> Path:
    """Minimal base catalog with one skill, one agent."""
    path = tmp_path / "base.yaml"
    path.write_text(
        "skills:\n"
        "  base-skill:\n"
        "    name: Base\n"
        "    description: base skill\n"
        "    tools: [t1]\n"
        "agent_skills:\n"
        "  agent-a:\n"
        "    - base-skill\n",
        encoding="utf-8",
    )
    return path


def test_overlay_adds_new_skill(tmp_path: Path) -> None:
    base = _write_base(tmp_path)
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(
        "skills:\n"
        "  extra:\n"
        "    name: Extra\n"
        "    description: added by overlay\n"
        "    tools: [t2]\n",
        encoding="utf-8",
    )
    skills, agents = load_catalog(base, overlay_path=overlay)
    assert set(skills.keys()) == {"base-skill", "extra"}
    assert skills["extra"].description == "added by overlay"
    # Agent list stays untouched when overlay only defines skills.
    assert agents == {"agent-a": ["base-skill"]}


def test_overlay_replaces_existing_skill(tmp_path: Path) -> None:
    base = _write_base(tmp_path)
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(
        "skills:\n"
        "  base-skill:\n"
        "    name: Base overridden\n"
        "    description: overridden by overlay\n"
        "    tools: [t-override]\n",
        encoding="utf-8",
    )
    skills, _ = load_catalog(base, overlay_path=overlay)
    assert skills["base-skill"].name == "Base overridden"
    assert skills["base-skill"].tools == ["t-override"]


def test_overlay_replaces_agent_skill_list(tmp_path: Path) -> None:
    """Overlay agent entries fully replace base lists — no implicit
    concat, to keep the merge rule predictable.
    """
    base = _write_base(tmp_path)
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(
        "skills:\n"
        "  extra:\n"
        "    name: Extra\n"
        "    description: added\n"
        "    tools: []\n"
        "agent_skills:\n"
        "  agent-a: [extra]\n",
        encoding="utf-8",
    )
    _, agents = load_catalog(base, overlay_path=overlay)
    assert agents["agent-a"] == ["extra"]


def test_overlay_adds_new_agent(tmp_path: Path) -> None:
    base = _write_base(tmp_path)
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(
        "agent_skills:\n"
        "  agent-b: [base-skill]\n",
        encoding="utf-8",
    )
    _, agents = load_catalog(base, overlay_path=overlay)
    assert set(agents.keys()) == {"agent-a", "agent-b"}
    assert agents["agent-b"] == ["base-skill"]


def test_env_var_overlay_is_applied(tmp_path: Path, monkeypatch) -> None:
    base = _write_base(tmp_path)
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(
        "skills:\n"
        "  from-env:\n"
        "    name: FromEnv\n"
        "    description: from env overlay\n"
        "    tools: []\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENTHUB_CATALOG_OVERLAY", str(overlay))
    skills, _ = load_catalog(base)
    assert "from-env" in skills


def test_env_var_overlay_missing_file_is_warning_not_error(
    tmp_path: Path, monkeypatch, caplog,
) -> None:
    """A typo in the overlay env var should log a warning and fall
    back to the base catalog rather than take the backend down.
    """
    base = _write_base(tmp_path)
    monkeypatch.setenv("AGENTHUB_CATALOG_OVERLAY", str(tmp_path / "missing.yaml"))
    with caplog.at_level("WARNING", logger="services.agenthub.catalog_loader"):
        skills, _ = load_catalog(base)
    assert "missing.yaml" in caplog.text
    # Base catalog still loaded.
    assert "base-skill" in skills


def test_overlay_path_false_disables_env_var(
    tmp_path: Path, monkeypatch,
) -> None:
    """Tests use ``overlay_path=False`` to ignore env var overlays the
    host environment might have set.
    """
    base = _write_base(tmp_path)
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(
        "skills:\n"
        "  from-env:\n"
        "    name: X\n"
        "    description: x\n"
        "    tools: []\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENTHUB_CATALOG_OVERLAY", str(overlay))
    skills, _ = load_catalog(base, overlay_path=False)
    assert "from-env" not in skills


def test_overlay_only_skills_section_is_legal(tmp_path: Path) -> None:
    """Overlays can omit agent_skills (or skills) entirely — base
    files cannot."""
    base = _write_base(tmp_path)
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(
        "skills:\n"
        "  extra:\n"
        "    name: Extra\n"
        "    description: d\n"
        "    tools: []\n",
        encoding="utf-8",
    )
    skills, _ = load_catalog(base, overlay_path=overlay)
    assert "extra" in skills


def test_empty_overlay_file_is_noop(tmp_path: Path) -> None:
    base = _write_base(tmp_path)
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text("", encoding="utf-8")
    skills, agents = load_catalog(base, overlay_path=overlay)
    assert "base-skill" in skills
    assert agents == {"agent-a": ["base-skill"]}
