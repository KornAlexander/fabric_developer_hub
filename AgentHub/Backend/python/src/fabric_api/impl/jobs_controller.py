import logging
import asyncio
from uuid import UUID
from typing import Set
from fabric_api.apis.jobs_api_base import BaseJobsApi
from fabric_api.models.create_item_job_instance_request import CreateItemJobInstanceRequest
from fabric_api.models.item_job_instance_state import ItemJobInstanceState
from fabric_api.models.job_instance_status import JobInstanceStatus
from fabric_api.models.job_invoke_type import JobInvokeType
from fabric_api.models.error_details import ErrorDetails
from fabric_api.models.error_source import ErrorSource

from services.authentication import get_authentication_service
from services.item_factory import get_item_factory

logger = logging.getLogger(__name__)

# # Global set to track background tasks
_background_tasks: Set[asyncio.Task] = set()

class JobsController(BaseJobsApi):
    """Implementation of the Jobs API for handling job lifecycle operations"""
    
    async def jobs_create_item_job_instance(
        self,
        workspaceId: UUID,
        itemType: str,
        itemId: UUID,
        jobType: str,
        jobInstanceId: UUID,
        activity_id: str = None,
        request_id: str = None,
        authorization: str = None,
        x_ms_client_tenant_id: str = None,
        create_item_job_instance_request: CreateItemJobInstanceRequest = None
    ) -> None:
        """Called by Microsoft Fabric for starting a new job instance."""
        logger.info(f"Creating job instance: {jobType}/{jobInstanceId} for item {itemType}/{itemId}")
        
        # Get required services
        auth_service = get_authentication_service()
        item_factory = get_item_factory()
        
        try:
            # Authenticate the call
            auth_context = await auth_service.authenticate_control_plane_call(
                authorization, 
                x_ms_client_tenant_id
            )
            
            # Create and load the item
            item = item_factory.create_item(itemType, auth_context)
            await item.load(itemId)
            
            logger.info(f"Running job type: {jobType}")
            
            # Start job execution in the background without awaiting it
            task = asyncio.create_task(
                self._execute_job_wrapper(
                    item,
                    jobType,
                    jobInstanceId,
                    create_item_job_instance_request.invoke_type if create_item_job_instance_request else None,
                    create_item_job_instance_request.creation_payload if create_item_job_instance_request else {}
                ),
                name=f"Job_{jobType}_{jobInstanceId}"
            )
            
            # Add to background tasks set to prevent garbage collection
            _background_tasks.add(task)
            
            # Remove from set when done
            task.add_done_callback(_background_tasks.discard)
            
            # Return 202 Accepted response (handled by FastAPI)
            logger.info(f"Job {jobInstanceId} started successfully")
            return None
        except Exception as e:
            logger.error(f"Error creating job instance: {str(e)}", exc_info=True)
            raise
    
    async def _execute_job_wrapper(self, item, job_type: str, job_instance_id: UUID, 
                                   invoke_type: JobInvokeType, creation_payload: dict):
        """Wrapper for job execution with proper error handling"""
        try:
            await item.execute_job(job_type, job_instance_id, invoke_type, creation_payload)
            logger.info(f"Job {job_instance_id} completed successfully")
        except asyncio.CancelledError:
            logger.warning(f"Job {job_instance_id} was cancelled during shutdown")
            raise  # Re-raise to properly handle cancellation
        except Exception as e:
            logger.error(f"Error during execution of job {job_instance_id} (type: {job_type}): {str(e)}", exc_info=True)
            # Don't re-raise - this is a background task
    
    async def jobs_get_item_job_instance_state(
        self,
        workspaceId: UUID,
        itemType: str,
        itemId: UUID,
        jobType: str,
        jobInstanceId: UUID,
        activity_id: str = None,
        request_id: str = None,
        authorization: str = None,
        x_ms_client_tenant_id: str = None
    ) -> ItemJobInstanceState:
        """Called by Microsoft Fabric for retrieving a job instance state."""
        logger.info(f"Getting job instance state: {jobType}/{jobInstanceId} for item {itemType}/{itemId}")
        
        # Get required services
        auth_service = get_authentication_service()
        item_factory = get_item_factory()
        
        try:
            # Authenticate the call
            auth_context = await auth_service.authenticate_control_plane_call(
                authorization, 
                x_ms_client_tenant_id
            )
            
            # Create and load the item
            item = item_factory.create_item(itemType, auth_context)
            await item.load(itemId)
            
            # Check if item exists
            if not item.item_object_id:
                logger.error(f"Item {itemId} not found")
                return ItemJobInstanceState(
                    status=JobInstanceStatus.FAILED,
                    error_details=ErrorDetails(
                        error_code="ItemNotFound",
                        message="Item not found.",
                        source=ErrorSource.SYSTEM
                    )
                )
            
            # Get job state
            job_state = await item.get_job_state(jobType, jobInstanceId)
            logger.info(f"Job {jobInstanceId} state: {job_state.status}")
            return job_state
        except Exception as e:
            logger.error(f"Error getting job instance state: {str(e)}", exc_info=True)
            raise
    
    async def jobs_cancel_item_job_instance(
        self,
        workspaceId: UUID,
        itemType: str,
        itemId: UUID,
        jobType: str,
        jobInstanceId: UUID,
        activity_id: str = None,
        request_id: str = None,
        authorization: str = None,
        x_ms_client_tenant_id: str = None
    ) -> ItemJobInstanceState:
        """Called by Microsoft Fabric for cancelling a job instance."""
        logger.info(f"Cancelling job instance: {jobType}/{jobInstanceId} for item {itemType}/{itemId}")
        
        # Get required services
        auth_service = get_authentication_service()
        item_factory = get_item_factory()
        
        try:
            # Authenticate the call
            auth_context = await auth_service.authenticate_control_plane_call(
                authorization, 
                x_ms_client_tenant_id
            )
            
            # Create and load the item
            item = item_factory.create_item(itemType, auth_context)
            await item.load(itemId)
            
            # Check if item exists
            if not item.item_object_id:
                logger.error(f"Item {itemId} not found")
                return ItemJobInstanceState(
                    status=JobInstanceStatus.FAILED,
                    error_details=ErrorDetails(
                        error_code="ItemNotFound",
                        message="Item not found.",
                        source=ErrorSource.SYSTEM
                    )
                )
            
            # Cancel the job
            logger.info(f"Canceling job {jobType}/{jobInstanceId}")
            await item.cancel_job(jobType, jobInstanceId)
            
            # Return canceled state
            return ItemJobInstanceState(
                status=JobInstanceStatus.CANCELLED
            )
        except Exception as e:
            logger.error(f"Error cancelling job instance: {str(e)}", exc_info=True)
            raise

async def cleanup_background_tasks(timeout: float = 3.0):
    """Clean up any remaining background tasks during shutdown."""
    if not _background_tasks:
        return
    
    pending_tasks = [task for task in _background_tasks if not task.done()]
    if not pending_tasks:
        _background_tasks.clear()
        return
    
    logger.info(f"Cancelling {len(pending_tasks)} pending background tasks...")
    
    # Cancel all pending tasks
    for task in pending_tasks:
        task.cancel()
    
    # Wait for cancellation with timeout
    try:
        await asyncio.wait_for(
            asyncio.gather(*pending_tasks, return_exceptions=True),
            timeout=timeout
        )
    except asyncio.TimeoutError:
        logger.warning(f"Some tasks did not complete within {timeout}s timeout")
    
    _background_tasks.clear()