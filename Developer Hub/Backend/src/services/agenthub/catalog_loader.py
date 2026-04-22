"""YAML loader for the AgentHub capability catalog.

Reads ``catalog.yaml`` (co-located in this package) and produces the
``SKILLS`` and ``AGENT_SKILLS`` shapes that ``agent_registry`` exposes.
This is Phase 2 of the redesign — the data moves out of Python, but the
runtime API (module-level ``SKILLS`` / ``_AGENT_SKILLS``) stays
identical so nothing downstream had to change.

Design choices:

* **One catalog file** rather than 15 per-skill files — keeps diff
  reviews and grep-based discovery trivial, matches the size of the
  catalog today (~15 skills, 7 agents).
* **Validation at load time** — a malformed catalog raises
  ``CatalogLoadError`` at import time so broken YAML can't make it to
  production silently. ``capability_registry.validate_catalog`` at
  boot then adds the cross-reference checks against live MCP tools.
* **Override-friendly path** — ``load_catalog(path)`` accepts an
  explicit file path, which keeps the module testable without
  monkey-patching the default location.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml

from domain.models.skill import Skill

DEFAULT_CATALOG_PATH = Path(__file__).parent / "catalog.yaml"

# Environment variable that points at an overlay YAML applied on top of
# the bundled base catalog. Used to layer per-environment skills (e.g.
# a "preview" tenant that gets early access to new skills) without
# forking the image.
OVERLAY_ENV_VAR = "AGENTHUB_CATALOG_OVERLAY"

logger = logging.getLogger(__name__)


class CatalogLoadError(RuntimeError):
    """Raised when ``catalog.yaml`` is missing, unparseable, or fails
    its structural validation.

    This is intentionally distinct from the runtime ``CapabilityIssue``
    findings — those are logged and non-fatal, while load errors
    indicate a broken deployment that must be fixed before the service
    can serve any agent traffic.
    """


def load_catalog(
    path: str | Path | None = None,
    *,
    overlay_path: str | Path | None = None,
) -> tuple[dict[str, Skill], dict[str, list[str]]]:
    """Parse the catalog YAML (plus optional overlay) and return
    ``(skills, agent_skills)``.

    Args:
        path: Optional base catalog override. Defaults to the bundled
            ``catalog.yaml`` sibling file.
        overlay_path: Optional overlay YAML merged on top of the base.
            When ``None``, the ``AGENTHUB_CATALOG_OVERLAY`` env var is
            consulted — callers who want to disable env-var overlays
            entirely (e.g. tests) should pass ``overlay_path=False``
            or use a private path.

    Merge semantics:
        * Skills: overlay skill entries replace base entries with the
          same id; new ids are added. No field-level merge — an
          overlay entry fully defines the skill.
        * Agents: overlay agent lists fully replace the base list. New
          agent ids are added. This keeps the merge predictable — no
          implicit ordering or deduplication magic.

    Returns:
        A tuple of:
          * ``skills`` — map of skill-id to a ``Skill`` pydantic model.
          * ``agent_skills`` — map of agent-id to the ordered list of
            skill-ids that agent declares.

    Raises:
        CatalogLoadError: base file missing, YAML parse failure, or any
            structural check fails (missing top-level keys, wrong
            types, duplicate skill ids). The cross-reference check
            (agents referencing unknown skills, skills referencing
            undiscovered tools) is intentionally NOT done here — that
            belongs to ``capability_registry.validate_catalog`` which
            has access to the live MCP tool set at boot.
    """
    base_path = Path(path) if path is not None else DEFAULT_CATALOG_PATH
    skills, agent_skills = _load_one(base_path, is_overlay=False)

    overlay = _resolve_overlay_path(overlay_path)
    if overlay is not None:
        ov_skills, ov_agents = _load_one(overlay, is_overlay=True)
        logger.info(
            "[CATALOG] applying overlay %s (+%d skills, +/~%d agents)",
            overlay, len(ov_skills), len(ov_agents),
        )
        skills.update(ov_skills)
        agent_skills.update(ov_agents)

    return skills, agent_skills


def _resolve_overlay_path(
    overlay_path: str | Path | None,
) -> Path | None:
    """Resolve the overlay path from an explicit arg, env var, or None.

    Returns ``None`` when no overlay is configured. Logs a warning but
    does not raise when the env var points at a missing file — a
    typo'd path shouldn't take the backend down, and the default
    catalog is a sensible fallback.
    """
    if overlay_path is False:
        return None
    if overlay_path is not None:
        p = Path(overlay_path)
        return p if p.exists() else None

    env_value = os.environ.get(OVERLAY_ENV_VAR)
    if not env_value:
        return None
    p = Path(env_value)
    if not p.exists():
        logger.warning(
            "[CATALOG] %s points at %s which does not exist; ignoring overlay",
            OVERLAY_ENV_VAR, p,
        )
        return None
    return p


def _load_one(
    catalog_path: Path,
    *,
    is_overlay: bool,
) -> tuple[dict[str, Skill], dict[str, list[str]]]:
    """Parse a single YAML file into the registry shape.

    Overlay files are allowed to omit either ``skills`` or
    ``agent_skills`` (the omitted section is treated as an empty
    mapping). Base files must define both.
    """
    if not catalog_path.exists():
        raise CatalogLoadError(
            f"capability catalog not found at {catalog_path}"
        )
    try:
        raw = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise CatalogLoadError(
            f"failed to parse {catalog_path}: {e}"
        ) from e

    if raw is None and is_overlay:
        # Empty overlay file is a legal no-op.
        return {}, {}
    if not isinstance(raw, dict):
        raise CatalogLoadError(
            f"{catalog_path}: top-level YAML must be a mapping, got "
            f"{type(raw).__name__}"
        )

    skills_raw = raw.get("skills")
    agents_raw = raw.get("agent_skills")

    if is_overlay:
        skills = _parse_skills(skills_raw, catalog_path) if skills_raw is not None else {}
        agent_skills = (
            _parse_agent_skills(agents_raw, catalog_path)
            if agents_raw is not None else {}
        )
    else:
        skills = _parse_skills(skills_raw, catalog_path)
        agent_skills = _parse_agent_skills(agents_raw, catalog_path)
    return skills, agent_skills


def _parse_skills(
    raw: Any,
    catalog_path: Path,
) -> dict[str, Skill]:
    if raw is None:
        raise CatalogLoadError(f"{catalog_path}: missing top-level 'skills' key")
    if not isinstance(raw, dict):
        raise CatalogLoadError(
            f"{catalog_path}: 'skills' must be a mapping, got {type(raw).__name__}"
        )

    out: dict[str, Skill] = {}
    for skill_id, fields in raw.items():
        if not isinstance(skill_id, str) or not skill_id:
            raise CatalogLoadError(
                f"{catalog_path}: skill id must be a non-empty string, got {skill_id!r}"
            )
        if not isinstance(fields, dict):
            raise CatalogLoadError(
                f"{catalog_path}: skill {skill_id!r} must be a mapping, "
                f"got {type(fields).__name__}"
            )
        try:
            skill = Skill(
                id=skill_id,
                name=fields["name"],
                description=_squash_whitespace(fields["description"]),
                tools=list(fields.get("tools") or []),
                applicable_when=_squash_whitespace(fields.get("applicable_when", "")),
            )
        except KeyError as e:
            raise CatalogLoadError(
                f"{catalog_path}: skill {skill_id!r} missing required field {e.args[0]!r}"
            ) from e
        except Exception as e:
            # pydantic ValidationError and friends — wrap with context
            # so the user sees which skill broke.
            raise CatalogLoadError(
                f"{catalog_path}: skill {skill_id!r} failed validation: {e}"
            ) from e
        out[skill_id] = skill
    return out


def _parse_agent_skills(
    raw: Any,
    catalog_path: Path,
) -> dict[str, list[str]]:
    if raw is None:
        raise CatalogLoadError(f"{catalog_path}: missing top-level 'agent_skills' key")
    if not isinstance(raw, dict):
        raise CatalogLoadError(
            f"{catalog_path}: 'agent_skills' must be a mapping, "
            f"got {type(raw).__name__}"
        )

    out: dict[str, list[str]] = {}
    for agent_id, skill_ids in raw.items():
        if not isinstance(agent_id, str) or not agent_id:
            raise CatalogLoadError(
                f"{catalog_path}: agent id must be a non-empty string, got {agent_id!r}"
            )
        if not isinstance(skill_ids, list) or not all(
            isinstance(s, str) and s for s in skill_ids
        ):
            raise CatalogLoadError(
                f"{catalog_path}: agent {agent_id!r} must map to a list of "
                f"non-empty skill-id strings"
            )
        out[agent_id] = list(skill_ids)
    return out


def _squash_whitespace(text: str) -> str:
    """Collapse YAML's folded-scalar whitespace into the single-spaced
    form the old Python literals used.

    Matters because the Skill model caps description at 600 chars and
    the compose LLM system prompt quotes these verbatim — stray runs
    of spaces or leftover newlines would inflate token count and
    potentially blow the cap.
    """
    return " ".join(text.split())
