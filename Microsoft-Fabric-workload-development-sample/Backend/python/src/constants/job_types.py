"""
Constants for job types used by the workload.
"""
from constants.workload_constants import WorkloadConstants

class Item1JobType:
    """Job types for Item1."""
    SCHEDULED_JOB = f"{WorkloadConstants.ItemTypes.ITEM1}.ScheduledJob"
    CALCULATE_AS_TEXT = f"{WorkloadConstants.ItemTypes.ITEM1}.CalculateAsText"
    CALCULATE_AS_PARQUET = f"{WorkloadConstants.ItemTypes.ITEM1}.CalculateAsParquet"
    LONG_RUNNING_CALCULATE_AS_TEXT = f"{WorkloadConstants.ItemTypes.ITEM1}.LongRunningCalculateAsText"
    INSTANT_JOB = f"{WorkloadConstants.ItemTypes.ITEM1}.InstantJob"