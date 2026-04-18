"""Unit tests for ``domain.items.agenthub_item.AgentHubItem``."""
from __future__ import annotations

from uuid import uuid4

import pytest

from domain.items.agenthub_item import PAYLOAD_KEY, AgentHubItem
from domain.models.agenthub_metadata import AgentHubMetadata
from fabric_api.models.job_instance_status import JobInstanceStatus
from fabric_api.models.job_invoke_type import JobInvokeType


@pytest.fixture
def item(mock_all_services, mock_auth_context) -> AgentHubItem:
    """AgentHubItem with the service-registry mocks wired up by conftest."""
    return AgentHubItem(mock_auth_context)


def test_item_type_is_agenthub(item: AgentHubItem) -> None:
    assert isinstance(item.item_type, str)
    assert item.item_type  # non-empty


def test_get_metadata_class_returns_agenthub_metadata(item: AgentHubItem) -> None:
    assert item.get_metadata_class() is AgentHubMetadata


def test_set_definition_empty_resets_metadata(item: AgentHubItem) -> None:
    item._metadata = AgentHubMetadata(default_model="gpt-5")
    item.set_definition({})
    assert item._metadata.default_model == "gpt-4o"  # default


def test_set_definition_with_payload_parses(item: AgentHubItem) -> None:
    item.set_definition({
        PAYLOAD_KEY: {
            "defaultModel": "gpt-5",
            "maxRounds": 7,
            "verboseDefault": False,
            "configuredAgents": [
                {"template_id": "t1", "display_name": "Agent 1"}
            ],
        }
    })
    assert item._metadata.default_model == "gpt-5"
    assert item._metadata.max_rounds == 7
    assert item._metadata.verbose_default is False
    assert len(item._metadata.configured_agents) == 1


def test_update_definition_patches_metadata(item: AgentHubItem) -> None:
    item.update_definition({PAYLOAD_KEY: {"maxRounds": 99}})
    assert item._metadata.max_rounds == 99


def test_update_definition_without_payload_key_is_noop(item: AgentHubItem) -> None:
    original = item._metadata
    item.update_definition({"other": 1})
    assert item._metadata is original


def test_get_type_specific_metadata_returns_alias_dict(item: AgentHubItem) -> None:
    out = item.get_type_specific_metadata()
    assert "defaultModel" in out  # camelCase alias, not snake_case
    assert out["defaultModel"] == "gpt-4o"


def test_set_type_specific_metadata_accepts_dict(item: AgentHubItem) -> None:
    item.set_type_specific_metadata({"defaultModel": "gpt-5"})
    assert item._metadata.default_model == "gpt-5"


def test_set_type_specific_metadata_accepts_model_instance(item: AgentHubItem) -> None:
    new_md = AgentHubMetadata(max_rounds=3)
    item.set_type_specific_metadata(new_md)
    assert item._metadata is new_md


def test_set_type_specific_metadata_ignores_unknown_types(item: AgentHubItem) -> None:
    original = item._metadata
    item.set_type_specific_metadata(None)
    item.set_type_specific_metadata("garbage")  # type: ignore[arg-type]
    assert item._metadata is original


@pytest.mark.asyncio
async def test_get_item_payload_wraps_metadata(item: AgentHubItem) -> None:
    item._metadata = AgentHubMetadata(default_model="gpt-5")
    payload = await item.get_item_payload()
    assert PAYLOAD_KEY in payload
    assert payload[PAYLOAD_KEY]["defaultModel"] == "gpt-5"


@pytest.mark.asyncio
async def test_execute_job_is_noop(item: AgentHubItem) -> None:
    await item.execute_job("run", uuid4(), JobInvokeType.MANUAL, {})


@pytest.mark.asyncio
async def test_get_job_state_returns_completed(item: AgentHubItem) -> None:
    state = await item.get_job_state("run", uuid4())
    assert state.status == JobInstanceStatus.COMPLETED


def test_agenthub_metadata_roundtrip() -> None:
    md = AgentHubMetadata.from_json_data({
        "defaultModel": "gpt-5",
        "maxRounds": 3,
    })
    assert md.default_model == "gpt-5"
    assert md.max_rounds == 3


def test_agenthub_metadata_empty_data_returns_defaults() -> None:
    md = AgentHubMetadata.from_json_data({})
    assert md.default_model == "gpt-4o"
    assert md.configured_agents == []
