"""Lock in that the Modeler agent owns the Power BI storage-mode decision
and that the FabricDataEngineer agent defers to it. These prompts are
load-bearing for picking Direct Lake vs Import vs DirectQuery vs
Composite, so a future prompt edit must not silently drop them.
"""

from __future__ import annotations

from services.agenthub.agent_registry import AGENT_TEMPLATES


def test_modeler_owns_storage_mode_decision() -> None:
    modeler = AGENT_TEMPLATES["modeler"]
    prompt = modeler.system_prompt
    assert "STORAGE_MODE:" in prompt
    for mode in ("Import", "DirectQuery", "Direct Lake", "Composite"):
        assert mode in prompt, f"Modeler prompt must reference {mode}"
    assert "OneLake" in prompt
    assert "calculated DAX DATATABLE" in prompt
    assert any(
        "storage mode" in entry.lower() for entry in modeler.boundaries.owns
    ), "Modeler must explicitly own the storage-mode decision"


def test_fabric_data_engineer_defers_storage_mode_to_modeler() -> None:
    builder = AGENT_TEMPLATES["fabric-data-engineer"]
    prompt = builder.system_prompt
    assert "Modeler" in prompt
    assert "Direct Lake" in prompt
    assert "DATATABLE" in prompt
