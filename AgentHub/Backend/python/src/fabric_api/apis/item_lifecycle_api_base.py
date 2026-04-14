# coding: utf-8

from typing import ClassVar, Dict, List, Tuple  # noqa: F401

from pydantic import Field, StrictStr
from typing import Any
from typing_extensions import Annotated
from fabric_api.models.create_item_request import CreateItemRequest
from fabric_api.models.error_response import ErrorResponse
from fabric_api.models.get_item_payload_response import GetItemPayloadResponse
from fabric_api.models.update_item_request import UpdateItemRequest


class BaseItemLifecycleApi:
    subclasses: ClassVar[Tuple] = ()

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        BaseItemLifecycleApi.subclasses = BaseItemLifecycleApi.subclasses + (cls,)
    async def item_lifecycle_create_item(
        self,
        workspaceId: Annotated[StrictStr, Field(description="The workspace ID.")],
        itemType: Annotated[StrictStr, Field(description="The item type.")],
        itemId: Annotated[StrictStr, Field(description="The item ID.")],
        activity_id: Annotated[StrictStr, Field(description="A unique ID for correlating the request with your system when a user interacts with your workload.")],
        request_id: Annotated[StrictStr, Field(description="A globally unique ID that helps Fabric correlate your request with our logs. Provide this ID when reporting an issue.")],
        authorization: Annotated[StrictStr, Field(description="A dual token authorization header that allows the workload to validate the request origin, provide user context, and call other services. This header has the following format: `SubjectAndAppToken1.0 subjectToken=\"delegated token\", appToken=\"S2S token\"`.")],
        x_ms_client_tenant_id: Annotated[StrictStr, Field(description="The tenant ID of the client making the request.")],
        create_item_request: Annotated[CreateItemRequest, Field(description="The item creation request.")],
    ) -> None:
        """Upon item creation Fabric performs basic validations, creates the item in a provisioning state and calls this API to notify the workload. The workload is expected to perform required validations, store the item metadata, allocate required resources, and update the Fabric item metadata cache with item relations and ETag.  This API should accept SubjectAndApp authentication.  ## Permissions  Permissions are checked by Microsoft Fabric."""
        ...


    async def item_lifecycle_delete_item(
        self,
        workspaceId: Annotated[StrictStr, Field(description="The workspace ID.")],
        itemType: Annotated[StrictStr, Field(description="The item type.")],
        itemId: Annotated[StrictStr, Field(description="The item ID.")],
        activity_id: Annotated[StrictStr, Field(description="A unique ID for correlating the request with your system when a user interacts with your workload.")],
        request_id: Annotated[StrictStr, Field(description="A globally unique ID that helps Fabric correlate your request with our logs. Provide this ID when reporting an issue.")],
        authorization: Annotated[StrictStr, Field(description="A dual token authorization header that allows the workload to validate the request origin, provide user context, and call other services. This header has the following format: `SubjectAndAppToken1.0 subjectToken=\"delegated token\", appToken=\"S2S token\"`.")],
        x_ms_client_tenant_id: Annotated[StrictStr, Field(description="The tenant ID of the client making the request.")],
    ) -> None:
        """Upon item deletion Fabric performs basic validations and calls this API to notify the workload. The workload is expected to delete the item metadata and free resources.   This API should accept SubjectAndApp authentication. However, the subject token may be unavailable in some cases.  ## Permissions  Permissions are checked by Microsoft Fabric."""
        ...


    async def item_lifecycle_get_item_payload(
        self,
        workspaceId: Annotated[StrictStr, Field(description="The workspace ID.")],
        itemType: Annotated[StrictStr, Field(description="The item type.")],
        itemId: Annotated[StrictStr, Field(description="The item ID.")],
        activity_id: Annotated[StrictStr, Field(description="A unique ID for correlating the request with your system when a user interacts with your workload.")],
        request_id: Annotated[StrictStr, Field(description="A globally unique ID that helps Fabric correlate your request with our logs. Provide this ID when reporting an issue.")],
        authorization: Annotated[StrictStr, Field(description="A dual token authorization header that allows the workload to validate the request origin, provide user context, and call other services. This header has the following format: `SubjectAndAppToken1.0 subjectToken=\"delegated token\", appToken=\"S2S token\"`.")],
        x_ms_client_tenant_id: Annotated[StrictStr, Field(description="The tenant ID of the client making the request.")],
    ) -> GetItemPayloadResponse:
        """When the item editor &amp;lt;iframe&amp;gt; requests an item, Fabric performs basic validations and calls this API to retrieve the payload from the workload.  This API accepts SubjectAndApp authentication.  ## Permissions  Permissions are checked by Microsoft Fabric."""
        ...


    async def item_lifecycle_update_item(
        self,
        workspaceId: Annotated[StrictStr, Field(description="The workspace ID.")],
        itemType: Annotated[StrictStr, Field(description="The item type.")],
        itemId: Annotated[StrictStr, Field(description="The item ID.")],
        activity_id: Annotated[StrictStr, Field(description="A unique ID for correlating the request with your system when a user interacts with your workload.")],
        request_id: Annotated[StrictStr, Field(description="A globally unique ID that helps Fabric correlate your request with our logs. Provide this ID when reporting an issue.")],
        authorization: Annotated[StrictStr, Field(description="A dual token authorization header that allows the workload to validate the request origin, provide user context, and call other services. This header has the following format: `SubjectAndAppToken1.0 subjectToken=\"delegated token\", appToken=\"S2S token\"`.")],
        x_ms_client_tenant_id: Annotated[StrictStr, Field(description="The tenant ID of the client making the request.")],
        update_item_request: Annotated[UpdateItemRequest, Field(description="The item update request.")],
    ) -> None:
        """Upon item update Fabric performs basic validations and calls this API to notify the workload. The workload is expected to perform required validations, store the item metadata, allocate and/or free resources, and update the Fabric item metadata cache with item relations and ETag.  This API should accept SubjectAndApp authentication.  ## Permissions  Permissions are checked by Microsoft Fabric."""
        ...
