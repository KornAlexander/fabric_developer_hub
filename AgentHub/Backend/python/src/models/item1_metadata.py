"""
Python implementation of Item1Metadata model.
"""
from enum import IntEnum
from typing import Any, Dict, Optional, TypeVar, Generic, ClassVar
from pydantic import BaseModel, Field, ConfigDict, field_serializer

from constants.item1_field_names import Item1FieldNames as Fields
from .fabric_item import  FabricItem
from .item_reference import ItemReference


class Item1Operator(IntEnum):
    UNDEFINED = 0
    ADD = 1
    SUBTRACT = 2
    MULTIPLY = 3
    DIVIDE = 4
    RANDOM = 5

    @classmethod
    def from_string(cls, value: str) -> 'Item1Operator':
        """Convert a string operator name to the enum value"""
        for member in cls:
            if (member.name.lower() == value.lower() or 
                member.name.capitalize() == value.capitalize()):
                return member
        raise ValueError(f"Unknown operator: {value}")
    
    @classmethod
    def _missing_(cls, value):
        """Handle string values by converting them to enum members"""
        if isinstance(value, str):
            # Try to find a case-insensitive match with the enum name
            for member in cls:
                if member.name.lower() == value.lower():
                    return member
                    
            # Try matching capitalized names (Add, Subtract, etc.)
            for member in cls:
                if member.name.capitalize() == value.capitalize():
                    return member
        elif isinstance(value, int):
            # Try to find a match with the enum value
            for member in cls:
                if member.value == value:
                    return member
        return None  # Let Python raise ValueError if no match

    def __str__(self) -> str:
        """Return a user-friendly string representation of the operator."""
        return self.name.capitalize()


# Generic type variable for the lakehouse reference
TLakehouse = TypeVar('TLakehouse')


class Item1MetadataBase(BaseModel, Generic[TLakehouse]):
    """
    Base class for Item1 metadata containing common properties.
    """
    operand1: int = Field(
        default=0,
        description="The first operand for the calculation"
    )
    operand2: int = Field(
        default=0,
        description="The second operand for the calculation"
    )
    operator: Item1Operator = Field(
        default=Item1Operator.UNDEFINED,
        description="The operation to perform on the operands"
    )
    lakehouse: Optional[TLakehouse] = Field(
        default=None,
        description="Reference to the lakehouse used by this item"
    )
    use_onelake: bool = Field(
        default=False,
        description="Flag indicating whether to use OneLake",
        alias="useOneLake"
    )
    last_calculation_result_location: Optional[str] = Field(
        default=None,
        description="The location of the last calculation result",
        alias="lastCalculationResultLocation"
    )
    
    model_config = ConfigDict(
        populate_by_name=True,
        use_enum_values=False,
    )

    @field_serializer('operator')
    def serialize_operator(self, value: Item1Operator) -> str:
        """Serialize Item1Operator to string."""
        return str(value)


class Item1Metadata(Item1MetadataBase[ItemReference]):
    """
    Represents the core metadata for item1 stored within the system's storage.
    """
    DEFAULT: ClassVar[Optional['Item1Metadata']] = None

    @classmethod
    def from_json_data(cls, metadata_dict: Dict[str, Any]) -> 'Item1Metadata':
        """
        Creates an Item1Metadata instance from a dictionary.
        Handles nested objects and type conversions.
        
        Args:
            metadata_dict: Dictionary containing metadata values
            
        Returns:
            An Item1Metadata instance populated with values from the dictionary
        """
        if not metadata_dict:
            return cls(lakehouse=ItemReference(workspace_id="", id=""))
        
        # Create lakehouse reference
        lakehouse_ref = ItemReference(workspace_id="", id="")
        if metadata_dict.get(Fields.LAKEHOUSE_FIELD):
            lakehouse_dict = metadata_dict[Fields.LAKEHOUSE_FIELD]
            workspace_id = lakehouse_dict.get(Fields.LAKEHOUSE_WORKSPACE_ID_FIELD)
            lakehouse_id = lakehouse_dict.get(Fields.LAKEHOUSE_ID_FIELD) or lakehouse_dict.get("id", "")
            lakehouse_ref = ItemReference(workspace_id=workspace_id, id=lakehouse_id)
        
        operator_value = metadata_dict.get(Fields.OPERATOR_FIELD, Item1Operator.UNDEFINED)
        try:
            operator = Item1Operator(operator_value)
        except ValueError:
            operator = Item1Operator.UNDEFINED
            
        return cls(
            operand1=metadata_dict.get(Fields.OPERAND1_FIELD, 0),
            operand2=metadata_dict.get(Fields.OPERAND2_FIELD, 0),
            operator=operator,
            lakehouse=lakehouse_ref,
            use_onelake=metadata_dict.get(Fields.USE_ONELAKE_FIELD, False),
            last_calculation_result_location=metadata_dict.get(
                Fields.RESULT_LOCATION_FIELD, "")
        )
    
    def clone(self) -> 'Item1Metadata':
        """
        Creates a clone of this Item1Metadata object.
        """
        #TODO: return deepcopy(self)
        return Item1Metadata(
            operand1=self.operand1,
            operand2=self.operand2,
            operator=self.operator,
            lakehouse=self.lakehouse,
            use_onelake=self.use_onelake,
            last_calculation_result_location=self.last_calculation_result_location
        )
    
    def is_valid_lakehouse(self) -> bool:
        """
        Check if the item has a valid lakehouse reference that can be used.
        
        Returns:
            bool: True if the lakehouse reference is valid and can be used, False otherwise.
        """
        return bool(self.lakehouse and 
                self.lakehouse.id and 
                self.lakehouse.id != "00000000-0000-0000-0000-000000000000" and
                self.lakehouse.workspace_id)
    
    def to_client_metadata(self, lakehouse: FabricItem) -> 'Item1ClientMetadata':
        """
        Converts this Item1Metadata to an Item1ClientMetadata object.
        Args:
            lakehouse: The FabricItem representing the lakehouse
            
        Returns:
            An Item1ClientMetadata object with properties from this object
        """
        if lakehouse is None:
            lakehouse_param = FabricItem(id="", workspace_id="", type="", display_name="")
        else:
            lakehouse_param = lakehouse
            
        return Item1ClientMetadata(
            operand1=self.operand1,
            operand2=self.operand2,
            operator=str(self.operator),
            lakehouse=lakehouse_param,
            use_onelake=self.use_onelake
        )


class Item1ClientMetadata(Item1MetadataBase[FabricItem]):
    """
    Represents extended metadata for item1, including additional information
    about the associated lakehouse, tailored for client-side usage.
    """
    pass


# Initialize the DEFAULT class variable
Item1Metadata.DEFAULT = Item1Metadata(lakehouse=ItemReference(id="", workspace_id=""))