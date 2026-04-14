class Item1FieldNames:
    """Constants for Item1 metadata field names."""
    # Payload structure fields
    PAYLOAD_METADATA = "item1Metadata"  # The key in the payload containing Item1 metadata

    # JSON/Client-side field names (camelCase)
    LAKEHOUSE_FIELD = "lakehouse"
    OPERAND1_FIELD = "operand1"
    OPERAND2_FIELD = "operand2"
    OPERATOR_FIELD = "operator"
    USE_ONELAKE_FIELD = "useOneLake"
    RESULT_LOCATION_FIELD = "lastCalculationResultLocation"
    
    # Nested field names   
    LAKEHOUSE_WORKSPACE_ID_FIELD = "workspaceId"
    LAKEHOUSE_ID_FIELD = "id"