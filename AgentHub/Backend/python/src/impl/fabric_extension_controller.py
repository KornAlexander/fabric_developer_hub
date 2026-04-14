import logging
from fastapi import APIRouter, Depends, Header, Path, HTTPException
from typing import Optional, List, Dict, Any
from uuid import UUID
from fastapi.responses import JSONResponse, PlainTextResponse
from services.authentication import AuthenticationService, get_authentication_service
from services.item_factory import ItemFactory, get_item_factory
from constants.workload_scopes import WorkloadScopes
from models.item1_metadata import Item1Operator
from constants.workload_constants import WorkloadConstants
from services.authorization import get_authorization_service

router = APIRouter(tags=["FabricExtension"])
logger = logging.getLogger(__name__)

@router.get("/item1SupportedOperators", response_model=List[str])
async def get_item1_supported_operators(
    authorization: Optional[str] = Header(None),
    auth_service: AuthenticationService = Depends(get_authentication_service)
):
    """
    Gets a list of arithmetic operators supported for Item1.
    This endpoint is called by loadSupportedOperators in SampleWorkloadEditor.
    """
    logger.info("Getting supported operators for Item1")
    
    # Authenticate the call
    try:

        await auth_service.authenticate_data_plane_call(
            authorization, 
            allowed_scopes=[WorkloadScopes.ITEM1_READ_WRITE_ALL]
        )
        
        operators = [op.name.title() for op in Item1Operator if op != Item1Operator.UNDEFINED]
        logger.info(f"Returning supported operators: {operators}")
        return operators
    except Exception as e:
        logger.error(f"Error getting supported operators: {e}")
        return JSONResponse(
            status_code=500,
            content={"message": f"Error getting supported operators:: {str(e)}"}
        )

@router.post("/{workspace_id}/{item_id}/item1DoubleResult")
async def item1_double_result(
    workspace_id: UUID,
    item_id: UUID,
    authorization: Optional[str] = Header(None),
    auth_service: AuthenticationService = Depends(get_authentication_service),
    item_factory: ItemFactory = Depends(get_item_factory)
):
    """Doubles the result of the calculation for an instance of Item1."""
    logger.info(f"Doubling result for Item1 {item_id}")
    
    auth_context = await auth_service.authenticate_data_plane_call(
        authorization, 
        allowed_scopes=[WorkloadScopes.ITEM1_READ_WRITE_ALL]
    )
     # Add this authorization check
    auth_handler = get_authorization_service()
    await auth_handler.validate_permissions(
        auth_context,
        workspace_id,
        item_id,
        ["Read", "Write"]
    )

    
    item = item_factory.create_item(WorkloadConstants.ItemTypes.ITEM1, auth_context)
    await item.load(item_id)
    operands = await item.double()
    
    return {"Operand1": operands[0], "Operand2": operands[1]}

@router.get("/{item_id}/getLastResult", response_class=PlainTextResponse)
async def get_last_result(
    item_id: UUID,
    authorization: Optional[str] = Header(None),
    auth_service: AuthenticationService = Depends(get_authentication_service),
    item_factory: ItemFactory = Depends(get_item_factory)
):
    """Get the last calculation result for an Item1."""
    logger.info(f"Getting last result for Item1 {item_id}")
    
    auth_context = await auth_service.authenticate_data_plane_call(
        authorization, 
        allowed_scopes=[WorkloadScopes.ITEM1_READ_WRITE_ALL]
    )
    logger.debug(f"getLastResut Using tenant ID: {auth_context.tenant_object_id}")

    try:
        item = item_factory.create_item(WorkloadConstants.ItemTypes.ITEM1, auth_context)
        await item.load(item_id)
        result = await item.get_last_result()
        return result
    except ValueError:  
        logger.warning(f"Item {item_id} not found, returning empty result")
        return ""
    except Exception as e:  
        logger.error(f"Error getting last result: {e}")
        return JSONResponse(
            status_code=500,
            content={"message": f"Error getting last result: {str(e)}"}
        )