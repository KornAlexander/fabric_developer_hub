from datetime import datetime, timezone
import json
import logging
import time
import random
import asyncio
from typing import Dict, Any, Optional, List, Tuple, Type, Union
from uuid import UUID

from .base_item import ItemBase
from exceptions.exceptions import AuthenticationUIRequiredException
from models.authentication_models import AuthorizationContext
from services.lakehouse_client_service import get_lakehouse_client_service
from fabric_api.models.job_invoke_type import JobInvokeType
from fabric_api.models.item_job_instance_state import ItemJobInstanceState
from fabric_api.models.job_instance_status import JobInstanceStatus
from constants.environment_constants import EnvironmentConstants
from constants.onelake_constants import OneLakeConstants
from constants.workload_constants import WorkloadConstants

from models.item1_metadata import Item1Operator
from constants.job_types import Item1JobType
from models.job_metadata import JobMetadata
from models.item_reference import ItemReference
from models.item1_metadata import Item1Metadata
from constants.item1_field_names import Item1FieldNames as Fields
from exceptions.exceptions import DoubledOperandsOverflowException, ItemMetadataNotFoundException


logger = logging.getLogger(__name__)



class Item1(ItemBase[Dict[str, Any], Dict[str, Any]]):
    # Static class variables
    supported_operators = [op.value for op in Item1Operator if op != Item1Operator.UNDEFINED]
    fabric_scopes = [f"{EnvironmentConstants.FABRIC_BACKEND_RESOURCE_ID}/Lakehouse.Read.All"]
    
    def __init__(self, auth_context: AuthorizationContext):
        """Initialize an Item1 instance."""
        super().__init__(auth_context)
        
        self._lakehouse_client_service = get_lakehouse_client_service()
        self._metadata = Item1Metadata()
        
    @property
    def item_type(self) -> str:
        return WorkloadConstants.ItemTypes.ITEM1
        
    @property
    def metadata(self) -> Item1Metadata:
        if not self._metadata:
            raise ValueError("The item object must be initialized before use")
        return self._metadata
        
    @property
    def lakehouse(self) -> ItemReference:
        return self.metadata.lakehouse
        
    @property
    def operand1(self) -> int:
        return self.metadata.operand1
        
    @property
    def operand2(self) -> int:
        return self.metadata.operand2
        
    @property
    def operator(self) -> str:
        return self.metadata.operator
    
    def get_metadata_class(self) -> Type[Item1Metadata]:
        """Return the metadata class for Item1."""
        return Item1Metadata
    
    def is_valid_lakehouse(self) -> bool:
        """
        Check if the item has a valid lakehouse reference that can be used.
        
        Returns:
            bool: True if the lakehouse reference is valid and can be used, False otherwise.
        """
        return self._metadata.is_valid_lakehouse()
        
    async def get_item_payload(self) -> Dict[str, Any]:
        """Get the item payload."""

        lakehouse_item = None
        # Try to get lakehouse details if we have a valid lakehouse reference
        if self.is_valid_lakehouse():
            try:
                token = await self.authentication_service.get_access_token_on_behalf_of(
                    self.auth_context, 
                    self.fabric_scopes
                )
                lakehouse_item = await self._lakehouse_client_service.get_fabric_lakehouse(
                    token,
                    self.lakehouse.workspace_id,
                    self.lakehouse.id
                )
            except Exception as e:
                self.logger.error(
                    f"Failed to retrieve FabricLakehouse for lakehouse: {self.lakehouse.id} "
                    f"in workspace: {self.lakehouse.workspace_id}. Error: {str(e)}"
                )
        
        client_metadata = self.metadata.to_client_metadata(lakehouse_item)
        return {Fields.PAYLOAD_METADATA: client_metadata}
        
    async def execute_job(self, 
                         job_type: str, 
                         job_instance_id: UUID, 
                         invoke_type: JobInvokeType, 
                         creation_payload: Dict[str, Any]) -> None:
        """Execute a job for this item."""
        if job_type.lower() == Item1JobType.INSTANT_JOB.lower():
            self.logger.info(f"Instant Job {job_instance_id} executed.")
            return
            
        # Create job metadata using JobMetadata class
        job_metadata = JobMetadata(
            job_type=job_type,
            job_instance_id=job_instance_id,
            use_onelake=self._metadata.use_onelake
        )
        
        # Store initial job metadata
        await self.item_metadata_store.upsert_job(
            self.tenant_object_id, 
            self.item_object_id, 
            str(job_instance_id), 
            job_metadata
        )

        token = await self.authentication_service.get_access_token_on_behalf_of(
                    self.auth_context,
                    OneLakeConstants.ONELAKE_SCOPES
                )
        
        # Fetch operands and operator from metadata
        op1 = self._metadata.operand1
        op2 = self._metadata.operand2
        calculation_operator = self._metadata.operator
        
        # Perform calculation
        result = self._calculate_result(op1, op2, calculation_operator)
    
        # Simulate long running job if needed
        if job_type.lower() == Item1JobType.LONG_RUNNING_CALCULATE_AS_TEXT.lower():
            await asyncio.sleep(480)  # 8 minutes
        
        # Reload job metadata to check if it was cancelled
        try:
            job_metadata = await self.item_metadata_store.load_job(
                self.tenant_object_id,
                self.item_object_id,
                str(job_instance_id)
            )
        except FileNotFoundError:
            # Recreate missing job metadata
            self.logger.warning(f"Recreating missing job {job_instance_id} metadata in tenant {self.tenant_object_id} item {self.item_object_id}")
            await self.item_metadata_store.upsert_job(
                self.tenant_object_id,
                self.item_object_id,
                str(job_instance_id),
                job_metadata
            )
        
         # Only proceed if not canceled 
        if not job_metadata.is_canceled:
            file_path = self._get_calculation_result_file_path(job_metadata)
            await self.onelake_client_service.write_to_onelake_file(token, file_path, result)
            self._metadata.last_calculation_result_location = file_path
            await self.save_changes()
            self.logger.info(f"Successfully saved result to OneLake at {file_path}")
        
    async def get_job_state(self, job_type: str, job_instance_id: UUID) -> ItemJobInstanceState:
        """Get the state of a job instance."""
        # For instant jobs, always return completed status immediately
        if job_type.lower() == Item1JobType.INSTANT_JOB.lower():
            return ItemJobInstanceState(status=JobInstanceStatus.COMPLETED)
            
        # Check if job metadata exists
        if not await self.item_metadata_store.exists_job(self.tenant_object_id, self.item_object_id, str(job_instance_id)):
            self.logger.error(
                f"Job {job_instance_id} metadata does not exist in tenant {self.tenant_object_id} "
                f"item {self.item_object_id}."
            )
            return ItemJobInstanceState(status=JobInstanceStatus.FAILED)
            
        # Load job metadata (now directly returns JobMetadata object)
        job_metadata = await self.item_metadata_store.load_job(
            self.tenant_object_id,
            self.item_object_id,
            str(job_instance_id)
        )
        
        # Check if job was canceled
        if job_metadata.is_canceled:
            return ItemJobInstanceState(status=JobInstanceStatus.CANCELLED)
    
        file_path = self._get_calculation_result_file_path(job_metadata)
            
        # Check if there's a locally saved result file
        try:
            token = await self.authentication_service.get_access_token_on_behalf_of(
                self.auth_context,
                OneLakeConstants.ONELAKE_SCOPES
            )
            file_exists = await self.onelake_client_service.check_if_file_exists(token, file_path)
            return ItemJobInstanceState(status=JobInstanceStatus.COMPLETED) if file_exists else ItemJobInstanceState(status=JobInstanceStatus.INPROGRESS)
        except Exception as token_ex:
            self.logger.error(f"Error checking OneLake file existence: {str(token_ex)}")
            # Continue to next check - don't fail the operation
            return ItemJobInstanceState(status=JobInstanceStatus.INPROGRESS)
        
    def _get_calculation_result_file_path(self, job_metadata: Union[Dict[str, Any], JobMetadata]) -> str:
        """Gets the path to the calculation result file in OneLake storage."""
        # Handle both Dict and JobMetadata for backward compatibility
        if isinstance(job_metadata, JobMetadata):
            job_instance_id = job_metadata.job_instance_id
            job_type = job_metadata.job_type
            use_onelake = job_metadata.use_onelake
        else:
            # Dictionary-based job metadata
            job_instance_id = job_metadata.get("job_instance_id")
            job_type = job_metadata.get("job_type", "")
            use_onelake = job_metadata.get("use_onelake", self.metadata.use_onelake)
            
        if not job_instance_id:
            error_msg = f"Cannot build calculation result file path: job_instance_id is missing in job metadata"
            self.logger.error(error_msg)
            raise ValueError(error_msg)
            
        #TODO: Refactor to use a job naames from config!
        type_to_filename = {
            Item1JobType.SCHEDULED_JOB.lower(): f"CalculationResult_{job_instance_id}.txt",
            Item1JobType.CALCULATE_AS_TEXT.lower(): f"CalculationResult_{job_instance_id}.txt",
            Item1JobType.LONG_RUNNING_CALCULATE_AS_TEXT.lower(): f"CalculationResult_{job_instance_id}.txt",
            Item1JobType.CALCULATE_AS_PARQUET.lower(): f"CalculationResult_{job_instance_id}.parquet"
        }
        
        # Get the filename based on job type or default to .txt
        job_type_lower = job_type.lower() if isinstance(job_type, str) else job_type
        filename = type_to_filename.get(job_type_lower, f"CalculationResult_{job_instance_id}.txt")
        
        # Determine the file path based on storage location choice
        if use_onelake:
            # Use OneLake storage
            return self.onelake_client_service.get_onelake_file_path(
            self.workspace_object_id,
            self.item_object_id,
            filename
        )
        else:
            if (self.metadata.lakehouse and
            self.metadata.lakehouse.id and 
            self.metadata.lakehouse.workspace_id and 
            self.metadata.lakehouse.id != "00000000-0000-0000-0000-000000000000"):
            # Use lakehouse path
                return self.onelake_client_service.get_onelake_file_path(
                self.metadata.lakehouse.workspace_id,
                self.metadata.lakehouse.id, 
                filename
            )
            
            else:
                error_msg = f"Cannot write to lakehouse or OneLake: missing lakehouse reference or useOneLake is false."
                self.logger.error(error_msg)
                raise ValueError(error_msg)
        
    def _calculate_result(self, op1: int, op2: int, calculation_operator: Union[str, Item1Operator]) -> str:
        """Calculate the result based on operands and operator."""
        op_enum: Item1Operator
        if isinstance(calculation_operator, str):
            try:
                op_enum = Item1Operator(calculation_operator) # Convert string to enum
            except ValueError as ve:
                raise ValueError(f"Unknown operator: {calculation_operator}")
        elif isinstance(calculation_operator, Item1Operator):
            op_enum = calculation_operator
        else:
            raise ValueError(f"Unknown operator: {calculation_operator}")
                
        
        """Calculate the result based on operands and operator."""
        if op_enum == Item1Operator.ADD:
            return self._format_result(op1, op2, op_enum, op1 + op2)
        elif op_enum == Item1Operator.SUBTRACT:
            return self._format_result(op1, op2, op_enum, op1 - op2)
        elif op_enum == Item1Operator.MULTIPLY:
            return self._format_result(op1, op2, op_enum, op1 * op2)
        elif op_enum == Item1Operator.DIVIDE:
            if op2 != 0:
                return self._format_result(op1, op2, op_enum, op1 // op2)
            else:
                raise ValueError("Cannot divide by zero.")
        elif op_enum == Item1Operator.RANDOM:
            if op1 > op2:
                raise ValueError("For RANDOM operator, operand1 must not be greater than operand2.")
            rand = random.randint(op1, op2)
            return self._format_result(op1, op2, op_enum, rand)
        elif op_enum == Item1Operator.UNDEFINED:
            raise ValueError("Undefined operator.")
        else:
            raise ValueError(f"Unsupported operator: {calculation_operator}")
            
    def _format_result(self, op1: int, op2: int, calculation_operator: Item1Operator, result: int) -> str:
        """Format the calculation result."""
        return f"op1 = {op1}, op2 = {op2}, operator = {calculation_operator.name.title()}, result = {result}"
        
    def _validate_operands_before_double(self, operand1: int, operand2: int) -> None:
        """Validate operands before doubling them."""
        invalid_operands = []
        
        if operand1 > 2**31 - 1 or operand1 < -2**31:
            invalid_operands.append("Operand1")
            
        if operand2 > 2**31 - 1 or operand2 < -2**31:
            invalid_operands.append("Operand2")
            
        if invalid_operands:
            raise DoubledOperandsOverflowException(invalid_operands)
            
    async def double(self) -> Tuple[int, int]:
        """Double the operands produced by the item calculation."""
        
        # Create metadata object from dict
        
        operand1 = self.metadata.operand1
        operand2 = self.metadata.operand2
        
        self._validate_operands_before_double(operand1, operand2)
        operand1 *= 2
        operand2 *= 2

        self.metadata.operand1 = operand1
        self.metadata.operand2 = operand2
        
        # Update the stored metadata
        await self.save_changes()   
        return (operand1, operand2)
        
    def set_definition(self, payload: Dict[str, Any]) -> None:
        """Set the item definition from a creation payload."""
        if not payload:
            self.logger.info(f"No payload is provided for {self.item_type}, objectId={self.item_object_id}")
            self._metadata = Item1Metadata()  
            return

        item1_metadata_json = payload.get(Fields.PAYLOAD_METADATA)
        if not item1_metadata_json:
            raise ValueError(f"Invalid item payload for type {self.item_type}, item ID {self.item_object_id}")
            
        lakehouse = item1_metadata_json.get(Fields.LAKEHOUSE_FIELD)
        use_onelake = item1_metadata_json.get(Fields.USE_ONELAKE_FIELD, False)

        if not lakehouse and not use_onelake:
            self.logger.error("Missing Lakehouse reference and useOneLake is false")
            raise ValueError(f"Missing Lakehouse reference for type {self.item_type}, item ID {self.item_object_id}")
        self.logger.debug(f"Set definition payload: {payload}")
        self._metadata = Item1Metadata.from_json_data(item1_metadata_json)
        self.logger.debug(f"Set definition metadata object: {self._metadata}")
        
        
    def update_definition(self, payload: Dict[str, Any]) -> None:
        """Update the item definition from an update payload."""
        self.logger.debug(f"Update payload: {payload}")
        if not payload:
            self.logger.info(f"No payload is provided for {self.item_type}, objectId={self.item_object_id}")
            return
        item1_metadata = payload.get(Fields.PAYLOAD_METADATA)
        if not item1_metadata:
            raise ValueError(f"Invalid item payload for type {self.item_type}, item ID {self.item_object_id}")

        lakehouse = item1_metadata.get(Fields.LAKEHOUSE_FIELD)
        use_onelake = item1_metadata.get(Fields.USE_ONELAKE_FIELD, False)

        if not lakehouse and not use_onelake:
            raise ValueError(f"Missing Lakehouse reference for type {self.item_type}, item ID {self.item_object_id}")

        #todo: fix!
        if lakehouse:
            lakehouse_workspace_id = lakehouse.get(Fields.LAKEHOUSE_WORKSPACE_ID_FIELD) or lakehouse.get("workspaceId")
            if not lakehouse_workspace_id:
                self.logger.error(f"ERROR! Something went wrong...")
                self.logger.error(f"workspace_object_id ({self.workspace_object_id}), fix null workspace_id in lakehouse metadata!")
                self.logger.error("Constructing metadata object will probvably fail")
        
        last_calculation_result_location = ""
        if self._metadata and self._metadata.last_calculation_result_location:
            last_calculation_result_location = self._metadata.last_calculation_result_location
        
        metadata = Item1Metadata.from_json_data(item1_metadata)
        metadata.last_calculation_result_location = last_calculation_result_location

        self.logger.debug(f"Update definition metadata OBJECT: {metadata}")
        self.set_type_specific_metadata(metadata)
        
        
    def set_type_specific_metadata(self, metadata: Item1Metadata) -> None:
        """Set the type-specific metadata for this item."""
        self._metadata = metadata.clone()
        
        
    def get_type_specific_metadata(self) -> Item1Metadata:
        """Get the type-specific metadata for this item."""
        return self._metadata.clone()
        
        
    async def get_last_result(self) -> str:
        """Get the last calculation result."""
        if not self.metadata.last_calculation_result_location or self.metadata.last_calculation_result_location.strip() == '':
            return ""
        try:
            token = await self.authentication_service.get_access_token_on_behalf_of(
                self.auth_context,
                OneLakeConstants.ONELAKE_SCOPES
            )
            
            return await self.onelake_client_service.get_onelake_file(
                token,
                self._metadata.last_calculation_result_location
            )
        except AuthenticationUIRequiredException:
            # Important: Re-raise AuthenticationUIRequiredException to ensure consent UI is triggered
            self.logger.warning("User consent required for OneLake access")
            raise
        except FileNotFoundError as file_ex:
            self.logger.error(f"File not found: {str(file_ex)}")
            return "" 
        except Exception as e:
            self.logger.error(f"Error getting last result: {str(e)}")
            return ""
        
        
    def _save_result_locally(self, job_instance_id: str, result: str) -> None:
        """Save calculation result locally as a fallback when OneLake is unavailable."""
        try:
            import os
            
            # Create results directory if it doesn't exist
            results_dir = os.path.join(os.getcwd(), "results")
            os.makedirs(results_dir, exist_ok=True)
            
            # Create a filename based on job instance ID
            filename = f"CalculationResult_{job_instance_id}.txt"
            file_path = os.path.join(results_dir, filename)
            
            # Write the result to a local file
            with open(file_path, "w") as f:
                f.write(result)
                
            # Update metadata with local file path
            self._metadata.last_calculation_result_location = file_path
            self.logger.info(f"Saved result locally to {file_path}")
        except Exception as e:
            self.logger.error(f"Failed to save result locally: {str(e)}")