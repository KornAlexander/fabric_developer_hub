from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict


class ConfiguredAgent(BaseModel):
    template_id: str = ""
    display_name: str = ""
    access_level: str = "read"
    enabled_tools: List[str] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class AgentHubMetadata(BaseModel):
    default_model: str = Field(default="gpt-4o", alias="defaultModel")
    max_rounds: int = Field(default=15, alias="maxRounds")
    verbose_default: bool = Field(default=True, alias="verboseDefault")
    configured_agents: List[ConfiguredAgent] = Field(
        default_factory=list, alias="configuredAgents"
    )

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def from_json_data(cls, data: Dict[str, Any]) -> "AgentHubMetadata":
        if not data:
            return cls()
        return cls.model_validate(data)
