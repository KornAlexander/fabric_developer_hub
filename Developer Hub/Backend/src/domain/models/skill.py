"""First-class Skill model for agent capabilities.

A Skill groups a set of tools an agent uses to accomplish a coherent
sub-capability. The compose LLM picks (agent_id, skill_ids[]) per slot;
the runtime narrows the tool surface the agent sees at execution time
to just the tools declared on the selected skills (union with a small
set of always-available tools like ``fabric_list_workspaces``).

This replaces the free-text ``tags`` field on ``AgentTemplate`` as the
discoverability surface for what an agent can actually do.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

_CAMEL_CONFIG = ConfigDict(
    alias_generator=to_camel,
    populate_by_name=True,
    extra="forbid",
)


class Skill(BaseModel):
    """A named capability an agent offers, mapped to the concrete tools
    it requires at runtime.
    """

    model_config = _CAMEL_CONFIG

    id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=600)
    # Tool ids (MCP tool names) the skill actually uses. Runtime union
    # with always-available tools when building the agent's tool
    # surface for a session.
    tools: list[str] = Field(default_factory=list)
    # Free-text heuristic to help the compose LLM decide when to pick
    # this skill. Not parsed by any code — used as part of the system
    # prompt.
    applicable_when: str = Field(default="", max_length=600)
