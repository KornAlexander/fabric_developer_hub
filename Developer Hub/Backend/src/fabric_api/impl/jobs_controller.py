import asyncio
import logging
from typing import Any
from uuid import UUID

from fabric_api.apis.jobs_api_base import BaseJobsApi
from fabric_api.models.create_item_job_instance_request import (
    CreateItemJobInstanceRequest,
)
from fabric_api.models.error_details import ErrorDetails
from fabric_api.models.error_source import ErrorSource
from fabric_api.models.item_job_instance_state import ItemJobInstanceState
from fabric_api.models.job_instance_status import JobInstanceStatus
from fabric_api.models.job_invoke_type import JobInvokeType
from services.auth.authentication import get_authentication_service
from services.fabric.item_factory import get_item_factory

logger = logging.getLogger(__name__)

# Module-level set tracks live background job tasks so they are not
# garbage-collected mid-flight (Python docs warn that asyncio holds only
# weak refs). Mutation happens from the asyncio thread only, so no lock
# is needed; cleanup_background_tasks() drains it on shutdown.
_background_tasks: set[asyncio.Task[None]] = set()


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
        logger.info(
            "Creating job instance: %s/%s for item %s/%s",
            jobType,
            jobInstanceId,
            itemType,
            itemId,
        )

        # Get required services
        auth_service = get_authentication_service()
        item_factory = get_item_factory()

        try:
            # Authenticate the call. authenticate_control_plane_call cross-
            # checks the tenant header against the bearer-token tenant claim.
            auth_context = await auth_service.authenticate_control_plane_call(
                authorization,
                x_ms_client_tenant_id
            )

            # Create and load the item — load() also enforces tenant isolation.
            item = item_factory.create_item(itemType, auth_context)
            await item.load(itemId)

            logger.info("Running job type: %s", jobType)

            # Start job execution in the background without awaiting it.
            # Track the task in _background_tasks to prevent GC and to allow
            # graceful cancellation during shutdown.
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

            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)

            # Return 202 Accepted (handled by FastAPI via empty return).
            logger.info("Job %s started successfully", jobInstanceId)
            return None
        except Exception:
            # Re-raise so the workload exception handler converts to a Fabric
            # error envelope. logger.exception adds the traceback.
            logger.exception("Error creating job instance")
            raise

    async def _execute_job_wrapper(
        self,
        item: Any,
        job_type: str,
        job_instance_id: UUID,
        invoke_type: JobInvokeType,
        creation_payload: dict[str, Any],
    ) -> None:
        """Wrapper for background job execution with proper error handling.

        Background-task contract:
        - On success: log and return.
        - On CancelledError: log and re-raise so the cancellation propagates.
        - On any other exception: log the traceback and SWALLOW. Re-raising
          would only surface as an "unhandled task exception" warning since
          nothing awaits this task.
        """
        try:
            await item.execute_job(job_type, job_instance_id, invoke_type, creation_payload)
            logger.info("Job %s completed successfully", job_instance_id)
        except asyncio.CancelledError:
            logger.warning("Job %s was cancelled during shutdown", job_instance_id)
            raise  # Re-raise to properly handle cancellation
        except Exception:
            logger.exception(
                "Error during execution of job %s (type: %s)",
                job_instance_id,
                job_type,
            )
            # Don't re-raise — this is a fire-and-forget background task.

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
        logger.info(
            "Getting job instance state: %s/%s for item %s/%s",
            jobType,
            jobInstanceId,
            itemType,
            itemId,
        )

        auth_service = get_authentication_service()
        item_factory = get_item_factory()

        try:
            auth_context = await auth_service.authenticate_control_plane_call(
                authorization,
                x_ms_client_tenant_id
            )

            item = item_factory.create_item(itemType, auth_context)
            await item.load(itemId)

            # Check if item exists
            if not item.item_object_id:
                logger.error("Item %s not found", itemId)
                return ItemJobInstanceState(
                    status=JobInstanceStatus.FAILED,
                    error_details=ErrorDetails(
                        error_code="ItemNotFound",
                        message="Item not found.",
                        source=ErrorSource.SYSTEM
                    )
                )

            job_state = await item.get_job_state(jobType, jobInstanceId)
            logger.info("Job %s state: %s", jobInstanceId, job_state.status)
            return job_state
        except Exception:
            logger.exception("Error getting job instance state")
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
        logger.info(
            "Cancelling job instance: %s/%s for item %s/%s",
            jobType,
            jobInstanceId,
            itemType,
            itemId,
        )

        auth_service = get_authentication_service()
        item_factory = get_item_factory()

        try:
            auth_context = await auth_service.authenticate_control_plane_call(
                authorization,
                x_ms_client_tenant_id
            )

            item = item_factory.create_item(itemType, auth_context)
            await item.load(itemId)

            # Check if item exists
            if not item.item_object_id:
                logger.error("Item %s not found", itemId)
                return ItemJobInstanceState(
                    status=JobInstanceStatus.FAILED,
                    error_details=ErrorDetails(
                        error_code="ItemNotFound",
                        message="Item not found.",
                        source=ErrorSource.SYSTEM
                    )
                )

            logger.info("Canceling job %s/%s", jobType, jobInstanceId)
            await item.cancel_job(jobType, jobInstanceId)

            return ItemJobInstanceState(
                status=JobInstanceStatus.CANCELLED
            )
        except Exception:
            logger.exception("Error cancelling job instance")
            raise


async def cleanup_background_tasks(timeout: float = 3.0) -> None:
    """Clean up any remaining background tasks during shutdown."""
    if not _background_tasks:
        return

    pending_tasks = [task for task in _background_tasks if not task.done()]
    if not pending_tasks:
        _background_tasks.clear()
        return

    logger.info("Cancelling %d pending background tasks...", len(pending_tasks))

    # Cancel all pending tasks
    for task in pending_tasks:
        task.cancel()

    # Wait for cancellation with timeout
    try:
        await asyncio.wait_for(
            asyncio.gather(*pending_tasks, return_exceptions=True),
            timeout=timeout
        )
    except TimeoutError:
        logger.warning("Some tasks did not complete within %.1fs timeout", timeout)

    _background_tasks.clear()
