import os


class WorkloadConstants:
    """Constants for the workload."""
    WORKLOAD_NAME = os.environ.get("WORKLOAD_NAME", "Org.AgentHub")

    class ItemTypes:
        """Nested class containing item type constants."""
        AGENTHUB_ITEM = None  # populated after class definition

WorkloadConstants.ItemTypes.AGENTHUB_ITEM = f"{WorkloadConstants.WORKLOAD_NAME}.AgentHubItem"
