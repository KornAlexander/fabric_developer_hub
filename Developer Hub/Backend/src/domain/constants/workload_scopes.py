from domain.constants.environment_constants import EnvironmentConstants


class WorkloadScopes:
    """Constants for OAuth scopes used in the workload."""
    FABRIC_BACKEND_RESOURCE_ID = EnvironmentConstants.FABRIC_BACKEND_RESOURCE_ID
    # AgentHub item scopes. NOTE: scope string values must match the scopes
    # exposed in the Entra app registration — do not change the strings without
    # also updating the app registration.
    AGENTHUB_READ_WRITE_ALL = "AgentHub.ReadWrite.All"
    AGENTHUB_READ_ALL = "AgentHub.Read.All"

    # Legacy aliases. The Microsoft workload sample that this repo forked
    # exposed its item-scope permissions as ``Item1.*``. Tenants whose Entra
    # app registration was configured from that sample (and not yet updated
    # to the ``AgentHub.*`` names) issue Fabric tokens with these legacy
    # scope strings. They represent the same permission — read/write the
    # workload's own items — so we accept them as aliases wherever
    # ``AGENTHUB_*`` is required. Remove once all deployments have updated
    # the Entra app's exposed-API list.
    ITEM1_READ_WRITE_ALL = "Item1.ReadWrite.All"
    ITEM1_READ_ALL = "Item1.Read.All"

    # Lakehouse scopes
    FABRIC_LAKEHOUSE_READ_ALL = "FabricLakehouse.Read.All"
    FABRIC_LAKEHOUSE_READ_WRITE_ALL = "FabricLakehouse.ReadWrite.All"

    FABRIC_WORKLOAD_CONTROL = "FabricWorkloadControl"
