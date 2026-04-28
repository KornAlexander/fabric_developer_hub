"""Tests for the compose service's JSON extraction and parse robustness.

These pin the defensive parsing layer that handles LLM responses that
are not clean JSON objects — markdown fences, leading prose, empty
responses, etc.
"""
from __future__ import annotations

import json

import pytest

from domain.models.composition import CompositionError
from services.agenthub.compose_service import _extract_json


# ── _extract_json ────────────────────────────────────────────────────

class TestExtractJson:
    def test_clean_json_passes_through(self):
        raw = '{"architecture": "solo", "slots": []}'
        assert _extract_json(raw) == raw

    def test_clean_json_with_whitespace(self):
        raw = '  \n  {"architecture": "solo"}\n  '
        assert json.loads(_extract_json(raw))["architecture"] == "solo"

    def test_markdown_fenced_json(self):
        raw = '```json\n{"architecture": "solo"}\n```'
        assert json.loads(_extract_json(raw))["architecture"] == "solo"

    def test_markdown_fenced_no_lang(self):
        raw = '```\n{"architecture": "solo"}\n```'
        assert json.loads(_extract_json(raw))["architecture"] == "solo"

    def test_markdown_fenced_with_surrounding_prose(self):
        raw = (
            "Here is the composition:\n\n"
            "```json\n"
            '{"architecture": "solo", "rationale": "test"}\n'
            "```\n\n"
            "Let me know if you need changes."
        )
        result = json.loads(_extract_json(raw))
        assert result["architecture"] == "solo"

    def test_prose_before_json(self):
        raw = (
            "I'll create a solo composition for this task.\n\n"
            '{"architecture": "solo", "slots": []}'
        )
        result = json.loads(_extract_json(raw))
        assert result["architecture"] == "solo"

    def test_prose_after_json(self):
        raw = (
            '{"architecture": "solo", "slots": []}\n\n'
            "This should work well for your task."
        )
        result = json.loads(_extract_json(raw))
        assert result["architecture"] == "solo"

    def test_empty_string_raises(self):
        with pytest.raises(CompositionError, match="empty response"):
            _extract_json("")

    def test_whitespace_only_raises(self):
        with pytest.raises(CompositionError, match="empty response"):
            _extract_json("   \n\t  ")

    def test_no_json_at_all_returns_original_for_error(self):
        raw = "I cannot complete this task because..."
        # Should return the text (so json.loads produces a precise error)
        result = _extract_json(raw)
        assert result == raw  # caller will get json.JSONDecodeError

    def test_nested_braces(self):
        raw = '{"architecture": "supervisor", "budget": {"maxTurns": 20}}'
        assert json.loads(_extract_json(raw))["architecture"] == "supervisor"

    def test_real_world_compose_output(self):
        """A realistic compose response with a full Composition."""
        raw = json.dumps({
            "architecture": "reflection",
            "rationale": "Single artifact production benefits from review",
            "headline": "Draft, review, and verify",
            "subtitle": "Actor writes the query, critic reviews it",
            "slots": [
                {"id": "actor", "agentId": "fabric-data-engineer", "role": "Query writer", "skills": []},
                {"id": "critic", "agentId": "fabric-data-engineer", "role": "Reviewer", "skills": []},
            ],
            "handoffs": [
                {"from": "actor", "to": "critic", "kind": "report"},
                {"from": "critic", "to": "actor", "kind": "critique"},
            ],
            "entrypointSlotId": "actor",
            "budget": {"maxTurns": 20, "maxToolCalls": 100, "maxWallclockS": 600, "requireApprovals": True},
        })
        result = json.loads(_extract_json(raw))
        assert result["architecture"] == "reflection"
        assert len(result["slots"]) == 2

    def test_bom_prefix_stripped(self):
        """Some HTTP responses include a BOM marker."""
        raw = '\ufeff{"architecture": "solo"}'
        result = json.loads(_extract_json(raw))
        assert result["architecture"] == "solo"


# ── ComposeService._parse robustness ─────────────────────────────────

class TestComposeServiceParse:
    """Tests that verify _parse handles edge cases from real LLM output."""

    def _make_service(self):
        """Create a ComposeService with a minimal agent catalog."""
        from domain.models.agent_models import AgentCategory, AgentTemplate
        from services.agenthub.compose_service import ComposeService
        agents = {
            "fabric-admin": AgentTemplate(
                id="fabric-admin", name="admin", display_name="Admin",
                category=AgentCategory.ADMIN, description="test",
                system_prompt="test", available_tools=[],
            ),
            "fabric-data-engineer": AgentTemplate(
                id="fabric-data-engineer", name="fde", display_name="FDE",
                category=AgentCategory.ENGINEERING, description="test",
                system_prompt="test", available_tools=[],
            ),
        }
        return ComposeService(agents=agents)

    def _valid_raw(self, **overrides) -> str:
        base = {
            "architecture": "solo",
            "rationale": "Simple task",
            "headline": "Do the thing",
            "subtitle": "One agent does it",
            "slots": [
                {"id": "s1", "agentId": "fabric-admin", "role": "Worker"},
            ],
            "handoffs": [],
            "entrypointSlotId": "s1",
            "budget": {"maxTurns": 10, "maxToolCalls": 50, "maxWallclockS": 300, "requireApprovals": True},
        }
        base.update(overrides)
        return json.dumps(base)

    def test_parse_clean_json(self):
        svc = self._make_service()
        comp = svc._parse(self._valid_raw(), session_id="s1", task="test", require_approvals=True)
        assert comp.architecture == "dynamic"
        assert comp.slots[0].agent_id == "generalist"

    def test_parse_markdown_fenced(self):
        svc = self._make_service()
        raw = "```json\n" + self._valid_raw() + "\n```"
        comp = svc._parse(raw, session_id="s1", task="test", require_approvals=True)
        assert comp.architecture == "dynamic"

    def test_parse_prose_wrapped(self):
        svc = self._make_service()
        raw = "Here is the composition:\n\n" + self._valid_raw() + "\n\nLet me know!"
        comp = svc._parse(raw, session_id="s1", task="test", require_approvals=True)
        assert comp.architecture == "dynamic"

    def test_parse_empty_raises_composition_error(self):
        svc = self._make_service()
        with pytest.raises(CompositionError):
            svc._parse("", session_id="s1", task="test", require_approvals=True)

    def test_parse_reflection_with_verify_kind(self):
        svc = self._make_service()
        raw = json.dumps({
            "architecture": "reflection",
            "rationale": "Needs review and verification",
            "headline": "Draft, review, verify",
            "subtitle": "Three-slot reflection",
            "slots": [
                {"id": "actor", "agentId": "fabric-data-engineer", "role": "Writer"},
                {"id": "critic", "agentId": "fabric-data-engineer", "role": "Reviewer"},
                {"id": "verifier", "agentId": "fabric-data-engineer", "role": "Verifier"},
            ],
            "handoffs": [
                {"from": "actor", "to": "critic", "kind": "report"},
                {"from": "critic", "to": "actor", "kind": "critique"},
                {"from": "critic", "to": "verifier", "kind": "verify"},
                {"from": "verifier", "to": "actor", "kind": "critique"},
            ],
            "entrypointSlotId": "actor",
            "budget": {"maxTurns": 20, "maxToolCalls": 100, "maxWallclockS": 600, "requireApprovals": True},
        })
        comp = svc._parse(raw, session_id="s1", task="test", require_approvals=True)
        assert comp.architecture == "dynamic"
        assert [slot.id for slot in comp.slots] == ["generalist"]
        assert comp.handoffs == []

    @pytest.mark.parametrize(
        "architecture",
        ["supervisor", "sequential", "hierarchical", "reflection", "mixed", "network"],
    )
    def test_parse_normalizes_legacy_one_slot_architectures(self, architecture):
        """Legacy topology ids normalize to the dynamic generalist seed."""
        svc = self._make_service()
        raw = self._valid_raw(architecture=architecture)
        comp = svc._parse(raw, session_id="s1", task="test", require_approvals=True)
        assert comp.architecture == "dynamic"
        assert [slot.id for slot in comp.slots] == ["generalist"]

    def test_parse_preserves_repeated_agent_templates_in_supervisor(self):
        """Supervisor fan-out may use the same agent template in multiple
        lead/worker roles. Those slots must not be collapsed away."""
        svc = self._make_service()
        raw = json.dumps({
            "architecture": "supervisor",
            "rationale": "Independent read-only audit facets need a lead and workers.",
            "headline": "Fan-out audit",
            "subtitle": "Lead delegates independent checks",
            "slots": [
                {"id": "lead", "agentId": "fabric-admin", "role": "Audit lead"},
                {"id": "inventory", "agentId": "fabric-admin", "role": "Inventory worker"},
                {"id": "flow", "agentId": "fabric-data-engineer", "role": "Data-flow worker"},
                {"id": "ops", "agentId": "fabric-admin", "role": "Operations worker"},
            ],
            "handoffs": [
                {"from": "lead", "to": "inventory", "kind": "delegate"},
                {"from": "lead", "to": "flow", "kind": "delegate"},
                {"from": "lead", "to": "ops", "kind": "delegate"},
            ],
            "entrypointSlotId": "lead",
            "budget": {"maxTurns": 20, "maxToolCalls": 100, "maxWallclockS": 600, "requireApprovals": True},
        })
        comp = svc._parse(raw, session_id="s1", task="test", require_approvals=True)
        assert comp.architecture == "dynamic"
        assert [s.id for s in comp.slots] == ["generalist"]
        assert comp.handoffs == []

    def test_parse_architecture_alias_actor_critic(self):
        """The LLM sometimes says 'actor-critic' instead of 'reflection'."""
        svc = self._make_service()
        raw = self._valid_raw(
            architecture="actor-critic",
            slots=[
                {"id": "actor", "agentId": "fabric-data-engineer", "role": "Writer"},
                {"id": "critic", "agentId": "fabric-data-engineer", "role": "Reviewer"},
            ],
            handoffs=[
                {"from": "actor", "to": "critic", "kind": "report"},
                {"from": "critic", "to": "actor", "kind": "critique"},
            ],
            entrypointSlotId="actor",
        )
        comp = svc._parse(raw, session_id="s1", task="test", require_approvals=True)
        assert comp.architecture == "dynamic"
        assert [s.id for s in comp.slots] == ["generalist"]
