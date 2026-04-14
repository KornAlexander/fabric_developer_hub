from constants.environment_constants import EnvironmentConstants
class WorkloadScopes:
    """Constants for OAuth scopes used in the workload."""
    FABRIC_BACKEND_RESOURCE_ID = EnvironmentConstants.FABRIC_BACKEND_RESOURCE_ID
    # Item1 scopes
    ITEM1_READ_WRITE_ALL = "Item1.ReadWrite.All"
    ITEM1_READ_ALL = "Item1.Read.All"
    
    # Lakehouse scopes
    FABRIC_LAKEHOUSE_READ_ALL = "FabricLakehouse.Read.All"
    FABRIC_LAKEHOUSE_READ_WRITE_ALL = "FabricLakehouse.ReadWrite.All"

    FABRIC_WORKLOAD_CONTROL = "FabricWorkloadControl"