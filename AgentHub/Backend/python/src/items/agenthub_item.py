import logging
from typing import Any
from uuid import UUID

from constants.workload_constants import WorkloadConstants
from fabric_api.models.item_job_instance_state import ItemJobInstanceState
from fabric_api.models.job_instance_status import JobInstanceStatus
from fabric_api.models.job_invoke_type import JobInvokeType
from items.base_item import ItemBase
from models.agenthub_metadata import AgentHubMetadata
from models.authentication_models import AuthorizationContext

logger = logging.getLogger(__name__)

PAYLOAD_KEY = "agenthub-metadata"


class AgentHubItem(ItemBase[dict[str, Any], dict[str, Any]]):
    """Fabric workspace item that persists AgentHub configuration (settings + agents)."""

    def __init__(self, auth_context: AuthorizationContext):
        super().__init__(auth_context)
        self._metadata = AgentHubMetadata()

    @property
    def item_type(self) -> str:
        return WorkloadConstants.ItemTypes.AGENTHUB_ITEM

    def get_metadata_class(self) -> type[AgentHubMetadata]:
        return AgentHubMetadata

    # ── Lifecycle hooks ───────────────────────────────────────────

    def set_definition(self, payload: dict[str, Any]) -> None:
        if payload and PAYLOAD_KEY in payload:
            self._metadata = AgentHubMetadata.from_json_data(payload[PAYLOAD_KEY])
        else:
            self._metadata = AgentHubMetadata()

    def update_definition(self, payload: dict[str, Any]) -> None:
        if payload and PAYLOAD_KEY in payload:
            self._metadata = AgentHubMetadata.from_json_data(payload[PAYLOAD_KEY])

    def get_type_specific_metadata(self) -> dict[str, Any]:
        return self._metadata.model_dump(by_alias=True)

    def set_type_specific_metadata(self, metadata) -> None:
        if isinstance(metadata, dict):
            self._metadata = AgentHubMetadata.model_validate(metadata)
        elif isinstance(metadata, AgentHubMetadata):
            self._metadata = metadata

    async def get_item_payload(self) -> dict[str, Any]:
        return {PAYLOAD_KEY: self._metadata.model_dump(by_alias=True)}

    # ── Jobs (delegated to orchestrator, no-op here) ──────────────

    async def execute_job(
        self,
        job_type: str,
        job_instance_id: UUID,
        invoke_type: JobInvokeType,
        creation_payload: dict[str, Any],
    ) -> None:
        logger.info("AgentHub job %s executed (no-op — orchestrator handles jobs).", job_instance_id)

    async def get_job_state(self, job_type: str, job_instance_id: UUID) -> ItemJobInstanceState:
        return ItemJobInstanceState(
            status=JobInstanceStatus.COMPLETED,
        )
