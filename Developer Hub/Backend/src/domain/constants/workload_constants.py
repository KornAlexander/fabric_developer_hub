import os
from typing import Final

# Resolved once at import time so the post-class assignment dance is not
# needed and the value is genuinely immutable for the lifetime of the process.
# The Fabric workload manifest pins this value — changing it requires a
# coordinated manifest re-publish.
_WORKLOAD_NAME: Final[str] = os.environ.get("WORKLOAD_NAME", "Org.AgentHub")


class WorkloadConstants:
    """Constants for the workload."""

    WORKLOAD_NAME: Final[str] = _WORKLOAD_NAME

    class ItemTypes:
        """Nested class containing item type constants."""

        AGENTHUB_ITEM: Final[str] = f"{_WORKLOAD_NAME}.AgentHubItem"
