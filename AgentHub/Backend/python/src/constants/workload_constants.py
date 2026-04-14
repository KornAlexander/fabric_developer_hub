import os


class WorkloadConstants:
    """Constants for the workload."""
    WORKLOAD_NAME = os.environ.get("WORKLOAD_NAME", "Org.AgentHub")

    class ItemTypes:
        """Nested class containing item type constants."""
        ITEM1 = None  # placeholder, will be set after class definition

WorkloadConstants.ItemTypes.ITEM1 = f"{WorkloadConstants.WORKLOAD_NAME}.SampleWorkloadItem"
    