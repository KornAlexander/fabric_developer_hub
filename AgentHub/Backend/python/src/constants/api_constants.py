from constants.environment_constants import EnvironmentConstants
class ApiConstants:
    """Api constants."""
    WORKLOAD_CONTROL_API_BASE_URL = f"{EnvironmentConstants.FABRIC_API_BASE_URL}/v1/workload-control"
    DEFAULT_OPENID_CONFIG_ENDPOINT = f"{EnvironmentConstants.AAD_INSTANCE_URL}/common/.well-known/openid-configuration"