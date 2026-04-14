from typing import Optional
from pydantic import BaseModel, Field

class LakehouseTable(BaseModel):
    """
    Model representing a table in a Lakehouse.
    """
    name: str = Field(
        ...,
        description="The name of the table"
    )
    path: str = Field(
        ...,
        description="The full path to the table in OneLake storage"
    )
    schema_name: Optional[str] = Field(
        None,
        description="The schema name of the table, if available",
        alias="schema"  
    )
    
    model_config = {
        "populate_by_name": True,
        "json_schema_extra": {
            "example": {
                "name": "customers",
                "path": "12345678-1234-5678-abcd-1234567890ab/Tables/customers/",
                "schema": "dbo"
            }
        }
    }