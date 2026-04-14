from typing import Any, Optional
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, Field


class JobMetadata(BaseModel):
    """
    Represents metadata for a job instance.
    """
    job_type: str
    job_instance_id: UUID
    error_details: Optional[Any] = None
    canceled_time: Optional[datetime] = None
    use_onelake: bool = False

    @property
    def is_canceled(self) -> bool:
        """Returns whether the job is canceled."""
        return self.canceled_time is not None

    def model_dump_json(self) -> dict:
        """Convert the job metadata to a dictionary for serialization.
        This maintains compatibility with the original to_dict method.
        """
        return {
            "job_type": self.job_type,
            "job_instance_id": str(self.job_instance_id),
            "error_details": self.error_details,
            "canceled_time": self.canceled_time.isoformat() if self.canceled_time else None,
            "use_onelake": self.use_onelake
        }
    
    def to_dict(self) -> dict:
        """Convert the job metadata to a dictionary for serialization."""
        return self.model_dump_json()
    
    @classmethod
    def from_dict(cls, data: dict) -> 'JobMetadata':
        """Create a JobMetadata instance from a dictionary."""
        return cls(
            job_type=data.get("job_type", ""),
            job_instance_id=UUID(data.get("job_instance_id", "00000000-0000-0000-0000-000000000000")),
            use_onelake=data.get("use_onelake", False),
            error_details=data.get("error_details"),
            canceled_time=datetime.fromisoformat(data["canceled_time"]) if data.get("canceled_time") else None
        )