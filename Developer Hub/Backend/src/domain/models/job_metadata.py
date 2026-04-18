from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class JobMetadata(BaseModel):
    """
    Represents metadata for a job instance.
    """

    job_type: str
    job_instance_id: UUID
    error_details: Any | None = None
    canceled_time: datetime | None = None
    use_onelake: bool = False

    @property
    def is_canceled(self) -> bool:
        """Returns whether the job is canceled."""
        return self.canceled_time is not None

    # NOTE: Do NOT override BaseModel.model_dump_json — Pydantic's contract is
    # that it returns a JSON string. The previous implementation returned a
    # dict, which silently broke any caller that relied on the standard
    # signature. Use to_dict() for the legacy dict shape used by the metadata
    # store and from_dict() for the inverse.

    def to_dict(self) -> dict[str, Any]:
        """Convert the job metadata to a dictionary for storage serialization.

        Stable wire shape used by the item_metadata_store; keeps snake_case
        field names exactly as the on-disk format expects.
        """
        return {
            "job_type": self.job_type,
            "job_instance_id": str(self.job_instance_id),
            "error_details": self.error_details,
            "canceled_time": self.canceled_time.isoformat() if self.canceled_time else None,
            "use_onelake": self.use_onelake,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JobMetadata":
        """Create a JobMetadata instance from a dictionary."""
        canceled_raw = data.get("canceled_time")
        return cls(
            job_type=data.get("job_type", ""),
            job_instance_id=UUID(
                data.get("job_instance_id", "00000000-0000-0000-0000-000000000000")
            ),
            use_onelake=data.get("use_onelake", False),
            error_details=data.get("error_details"),
            canceled_time=datetime.fromisoformat(canceled_raw) if canceled_raw else None,
        )
