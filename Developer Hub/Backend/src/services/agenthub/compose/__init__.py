"""Modular composer internals.

The composer's system prompt used to be one monolithic f-string with
hard-coded agent names, hard-coded architecture rules, and hard-coded
task-type recipes. Every change (new agent, new flow, new recipe)
required editing that prompt by hand.

This package replaces that monolith with three data sources +
one pure assembler:

* ``services.agenthub.agent_registry``  — agents with structured
  :class:`AgentBoundaries`.
* ``domain.catalogs.architectures``     — architectures with
  structured ``slot_rules``.
* ``services.agenthub.compose.recipes`` — declarative
  :class:`CompositionRecipe` catalog.
* ``services.agenthub.compose.prompt``  — ``build_system_prompt()``
  assembles the four sections at render time from pure functions over
  the catalogs. No hard-coded agent ids or architecture ids.

Adding a new agent, architecture, or recipe is now a data change, not
a prompt edit.
"""

from services.agenthub.compose.prompt import build_system_prompt
from services.agenthub.compose.recipes import RECIPES, CompositionRecipe

__all__ = [
    "RECIPES",
    "CompositionRecipe",
    "build_system_prompt",
]
