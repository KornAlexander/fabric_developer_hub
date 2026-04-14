# coding: utf-8

from typing import ClassVar, Dict, List, Tuple  # noqa: F401

from pydantic import Field, StrictStr
from typing import Any
from typing_extensions import Annotated
from fabric_api.models.create_item_job_instance_request import CreateItemJobInstanceRequest
from fabric_api.models.error_response import ErrorResponse
from fabric_api.models.item_job_instance_state import ItemJobInstanceState


class BaseJobsApi:
    subclasses: ClassVar[Tuple] = ()

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        BaseJobsApi.subclasses = BaseJobsApi.subclasses + (cls,)
    async def jobs_cancel_item_job_instance(
        self,
        workspaceId: Annotated[StrictStr, Field(description="The workspace ID.")],
        itemType: Annotated[StrictStr, Field(description="The item type.")],
        itemId: Annotated[StrictStr, Field(description="The item ID.")],
        jobType: Annotated[StrictStr, Field(description="The job type.")],
        jobInstanceId: Annotated[StrictStr, Field(description="The job instance ID.")],
        activity_id: Annotated[StrictStr, Field(description="A unique ID for correlating the request with your system when a user interacts with your workload.")],
        request_id: Annotated[StrictStr, Field(description="A globally unique ID that helps Fabric correlate your request with our logs. Provide this ID when reporting an issue.")],
        authorization: Annotated[StrictStr, Field(description="A dual token authorization header that allows the workload to validate the request origin, provide user context, and call other services. This header has the following format: `SubjectAndAppToken1.0 subjectToken=\"delegated token\", appToken=\"S2S token\"`.")],
        x_ms_client_tenant_id: Annotated[StrictStr, Field(description="The tenant ID of the client making the request.")],
    ) -> ItemJobInstanceState:
        """Fabric performs basic validations and calls this API to cancel an item job instance in the workload.  This API should accept SubjectAndApp authentication.  ## Permissions  Permissions are checked by Microsoft Fabric."""
        ...


    async def jobs_create_item_job_instance(
        self,
        workspaceId: Annotated[StrictStr, Field(description="The workspace ID.")],
        itemType: Annotated[StrictStr, Field(description="The item type.")],
        itemId: Annotated[StrictStr, Field(description="The item ID.")],
        jobType: Annotated[StrictStr, Field(description="The job type.")],
        jobInstanceId: Annotated[StrictStr, Field(description="The job instance ID.")],
        activity_id: Annotated[StrictStr, Field(description="A unique ID for correlating the request with your system when a user interacts with your workload.")],
        request_id: Annotated[StrictStr, Field(description="A globally unique ID that helps Fabric correlate your request with our logs. Provide this ID when reporting an issue.")],
        authorization: Annotated[StrictStr, Field(description="A dual token authorization header that allows the workload to validate the request origin, provide user context, and call other services. This header has the following format: `SubjectAndAppToken1.0 subjectToken=\"delegated token\", appToken=\"S2S token\"`.")],
        x_ms_client_tenant_id: Annotated[StrictStr, Field(description="The tenant ID of the client making the request.")],
        create_item_job_instance_request: Annotated[CreateItemJobInstanceRequest, Field(description="The job instance properties.")],
    ) -> None:
        """Fabric performs basic validations and calls this API to start a new instance of the job in the workload.  This API should accept SubjectAndApp authentication.  ## Permissions  Permissions are checked by Microsoft Fabric."""
        ...


    async def jobs_get_item_job_instance_state(
        self,
        workspaceId: Annotated[StrictStr, Field(description="The workspace ID.")],
        itemType: Annotated[StrictStr, Field(description="The item type.")],
        itemId: Annotated[StrictStr, Field(description="The item ID.")],
        jobType: Annotated[StrictStr, Field(description="The job type.")],
        jobInstanceId: Annotated[StrictStr, Field(description="The job instance ID.")],
        activity_id: Annotated[StrictStr, Field(description="A unique ID for correlating the request with your system when a user interacts with your workload.")],
        request_id: Annotated[StrictStr, Field(description="A globally unique ID that helps Fabric correlate your request with our logs. Provide this ID when reporting an issue.")],
        authorization: Annotated[StrictStr, Field(description="A dual token authorization header that allows the workload to validate the request origin, provide user context, and call other services. This header has the following format: `SubjectAndAppToken1.0 subjectToken=\"delegated token\", appToken=\"S2S token\"`.")],
        x_ms_client_tenant_id: Annotated[StrictStr, Field(description="The tenant ID of the client making the request.")],
    ) -> ItemJobInstanceState:
        """Fabric performs basic validations and calls this API to retrieve the item job instance state in the workload.  This API should accept SubjectAndApp authentication.  ## Permissions  Permissions are checked by Microsoft Fabric."""
        ...
