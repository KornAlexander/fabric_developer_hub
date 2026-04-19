"""Manifest consistency / regression tests.

Guards against the "Invalid job type detected or too many ArtifactJobTypes"
BadRequest that Fabric's dev-gateway returns during ``Registering dev
instance`` when the frontend item JSON or backend item XML are malformed.

Covers two distinct concepts that look confusingly similar:

1. Backend ``manifest/<Item>.xml`` ``<JobScheduler><ItemJobTypes>`` —
   fully-qualified scheduled/on-demand workload job names (4 dotted segments:
   ``{Publisher}.{WorkloadName}.{ItemName}.{JobTypeName}``).
2. Frontend ``Package/<Item>.json`` top-level ``itemJobTypes`` array —
   data-access operation tags (e.g. ``getData``, ``storeData``). It MUST NOT
   contain scheduled-job names; doing so triggers the BadRequest above.

Any change to these files must keep both rules intact. These tests run at
unit-test time so regressions are caught before the manifest package is
rebuilt and shipped to the dev-gateway.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from string import Template
from xml.etree import ElementTree as ET

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_DEVHUB_ROOT = Path(__file__).resolve().parents[4]
_BACKEND_MANIFEST_DIR = _DEVHUB_ROOT / "Backend" / "manifest"
_FRONTEND_PACKAGE_DIR = _DEVHUB_ROOT / "Frontend" / "Package"

_ITEM_XML = _BACKEND_MANIFEST_DIR / "AgentHubItem.xml"
_ITEM_JSON = _FRONTEND_PACKAGE_DIR / "AgentHubItem.json"
_WORKLOAD_XML = _BACKEND_MANIFEST_DIR / "WorkloadManifest.xml"

# Names that Fabric reserves / that are implicit from other manifest fields
# and therefore MUST NOT appear in the frontend JSON's top-level
# ``itemJobTypes`` array. Extend this set whenever we discover a new
# forbidden value.
_FORBIDDEN_FRONTEND_JOB_TYPE_NAMES = frozenset(
    {
        "ScheduledJob",
        "InstantJob",
    }
)

# XSD ``JobTypeName`` pattern (see Backend/manifest/ItemDefinition.xsd).
_JOB_TYPE_NAME_RE = re.compile(r"^[A-Za-z0-9-]+\.[A-Za-z0-9-]+\.[A-Za-z0-9-]+\.[A-Za-z0-9-]+$")

_PLACEHOLDER_WORKLOAD_NAME = "Org.FabricClawHub"
_PLACEHOLDER_ITEM_NAME = "AgentHubItem"


def _load_item_xml_text() -> str:
    """Read AgentHubItem.xml and substitute ${WORKLOAD_NAME} with a fixed
    test value so the file is parseable and name-checkable even if .env
    isn't loaded into the test process."""
    raw = _ITEM_XML.read_text(encoding="utf-8")
    return Template(raw).safe_substitute(WORKLOAD_NAME=_PLACEHOLDER_WORKLOAD_NAME)


def _load_item_json() -> dict:
    return json.loads(_ITEM_JSON.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFrontendItemJobTypes:
    """Frontend ``itemJobTypes`` array (data-operation tags)."""

    def test_field_is_absent_or_list(self) -> None:
        data = _load_item_json()
        if "itemJobTypes" in data:
            assert isinstance(data["itemJobTypes"], list), (
                "'itemJobTypes' must be a JSON array when present"
            )

    def test_does_not_contain_scheduler_job_names(self) -> None:
        """The frontend ``itemJobTypes`` array MUST NOT contain scheduled-job
        short names — those belong only in the backend XML ``<JobScheduler>``.

        Regression: dev-gateway ``BadRequest: "Invalid job type detected or
        too many ArtifactJobTypes" (Target: ArtifactJobTypes)`` during Dev
        instance registration. See comment block in
        ``Backend/manifest/AgentHubItem.xml``.
        """
        data = _load_item_json()
        entries = set(data.get("itemJobTypes", []) or [])
        forbidden = entries & _FORBIDDEN_FRONTEND_JOB_TYPE_NAMES
        assert not forbidden, (
            "Frontend Package/AgentHubItem.json 'itemJobTypes' MUST NOT "
            "contain scheduled-job short names. Found: "
            f"{sorted(forbidden)!r}. These belong only in the backend "
            "manifest XML <JobScheduler><ItemJobTypes>. Putting them here "
            "makes Fabric's dev-gateway reject registration with "
            "'Invalid job type detected or too many ArtifactJobTypes'."
        )

    def test_schedule_item_job_type_matches_xml(self) -> None:
        """When ``itemSettings.schedule.itemJobType`` is declared in the
        frontend JSON, the backend XML ``<JobScheduler>`` must declare a
        matching qualified ``<ItemJobType>`` entry."""
        data = _load_item_json()
        schedule = (data.get("itemSettings") or {}).get("schedule")
        if not schedule or "itemJobType" not in schedule:
            pytest.skip("No itemSettings.schedule.itemJobType configured.")

        short_name = schedule["itemJobType"]
        xml_names = _xml_item_job_type_names()
        expected_suffix = f".{_PLACEHOLDER_ITEM_NAME}.{short_name}"
        assert any(n.endswith(expected_suffix) for n in xml_names), (
            f"itemSettings.schedule.itemJobType={short_name!r} has no "
            f"matching <ItemJobType Name='...{expected_suffix}' /> in "
            f"Backend/manifest/AgentHubItem.xml. Declared XML entries: "
            f"{sorted(xml_names)!r}."
        )


def _xml_item_job_type_names() -> set[str]:
    root = ET.fromstring(_load_item_xml_text())
    return {e.attrib["Name"] for e in root.iter("ItemJobType")}


class TestBackendItemJobScheduler:
    """Backend XML ``<JobScheduler><ItemJobTypes>`` — qualified workload
    job names."""

    def test_xml_parses(self) -> None:
        ET.fromstring(_load_item_xml_text())  # raises if malformed

    def test_at_least_one_job_type_declared(self) -> None:
        names = _xml_item_job_type_names()
        assert names, "Backend XML must declare ≥1 <ItemJobType>."

    def test_every_name_matches_xsd_pattern(self) -> None:
        """Each ``Name`` attribute must be 4 dotted segments per the
        XSD ``JobTypeName`` pattern."""
        bad = [n for n in _xml_item_job_type_names() if not _JOB_TYPE_NAME_RE.match(n)]
        assert not bad, (
            f"ItemJobType Name values do not match XSD JobTypeName "
            f"pattern (Publisher.Workload.Item.Job): {bad!r}"
        )

    def test_every_name_prefixed_with_workload_and_item(self) -> None:
        expected_prefix = f"{_PLACEHOLDER_WORKLOAD_NAME}.{_PLACEHOLDER_ITEM_NAME}."
        bad = [n for n in _xml_item_job_type_names() if not n.startswith(expected_prefix)]
        assert not bad, (
            f"ItemJobType Name values must be prefixed with "
            f"{expected_prefix!r}; got: {bad!r}"
        )

    def test_no_duplicate_names(self) -> None:
        root = ET.fromstring(_load_item_xml_text())
        names = [e.attrib["Name"] for e in root.iter("ItemJobType")]
        assert len(names) == len(set(names)), (
            f"Duplicate ItemJobType names in backend XML: {names!r}"
        )
