"""
FastAPI dependency injection helpers.
These provide clean interfaces for injecting services into controllers.
"""
from typing import Annotated

from fastapi import Depends

from services.auth.authentication import (
    AuthenticationService,
    get_authentication_service,
)
from services.auth.authorization import AuthorizationHandler, get_authorization_service
from services.fabric.item_factory import ItemFactory, get_item_factory
from services.fabric.item_metadata_store import (
    ItemMetadataStore,
    get_item_metadata_store,
)
from services.fabric.lakehouse_client_service import (
    LakehouseClientService,
    get_lakehouse_client_service,
)
from services.fabric.onelake_client_service import (
    OneLakeClientService,
    get_onelake_client_service,
)
from services.http_client import HttpClientService, get_http_client_service

# Type aliases for cleaner dependency injection
AuthServiceDep = Annotated[AuthenticationService, Depends(get_authentication_service)]
AuthHandlerDep = Annotated[AuthorizationHandler, Depends(get_authorization_service)]
ItemFactoryDep = Annotated[ItemFactory, Depends(get_item_factory)]
ItemMetadataStoreDep = Annotated[ItemMetadataStore, Depends(get_item_metadata_store)]
HttpClientDep = Annotated[HttpClientService, Depends(get_http_client_service)]
LakehouseClientDep = Annotated[LakehouseClientService, Depends(get_lakehouse_client_service)]
OneLakeClientDep = Annotated[OneLakeClientService, Depends(get_onelake_client_service)]
