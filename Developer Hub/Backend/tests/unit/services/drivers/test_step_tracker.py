"""Tests for the StepTracker observability system."""
from __future__ import annotations

import logging

import pytest

from services.agenthub.drivers.step_tracker import StepTracker


class TestStepTracker:
    def test_sequential_numbering(self):
        t = StepTracker("sess-12345678", "sequential")
        t.register_names({"s1": "Architect", "s2": "Modeler", "s3": "FDE"})

        assert t.next_step() == 1
        assert t.seq_label("s1") == "STEP 1 · Architect"

        assert t.next_step() == 2
        assert t.seq_label("s2") == "STEP 2 · Modeler"

        assert t.next_step() == 3
        assert t.seq_label("s3") == "STEP 3 · FDE"

    def test_parallel_labels(self):
        t = StepTracker("sess-12345678", "supervisor")
        t.register_names({"w1": "Worker1", "w2": "Worker2", "w3": "Worker3"})

        t.next_step()  # step 1 (lead)
        t.next_step()  # step 2 (workers)
        labels = t.parallel_labels(["w1", "w2", "w3"])
        assert labels["w1"] == "STEP 2-A · Worker1"
        assert labels["w2"] == "STEP 2-B · Worker2"
        assert labels["w3"] == "STEP 2-C · Worker3"

    def test_sub_labels(self):
        t = StepTracker("sess-12345678", "hierarchical")
        t.register_names({"sub1": "SubLead1", "sub1-w1": "Worker1"})

        t.next_step()  # step 1
        assert t.sub_label("A", 1, "sub1-w1") == "STEP 1-A.1 · Worker1"
        assert t.sub_label("A", 2, "sub1-w1") == "STEP 1-A.2 · Worker1"

    def test_unknown_slot_falls_back_to_id(self):
        t = StepTracker("sess-12345678", "solo")
        t.next_step()
        assert t.seq_label("unknown-slot") == "STEP 1 · unknown-slot"

    def test_session_summary_tracking(self, caplog):
        t = StepTracker("sess-12345678", "sequential")
        t.register_names({"s1": "A", "s2": "B", "s3": "C"})

        # Simulate: s1 completed, s2 failed, s3 skipped
        t.log_slot_done("STEP 1 · A", "s1", status="success", rounds=1, tools=0, duration_s=5.0)
        t.log_slot_done("STEP 2 · B", "s2", status="error", rounds=2, tools=1, duration_s=10.0)
        t.log_slot_skipped("s3", "upstream_failed")

        with caplog.at_level(logging.INFO):
            t.log_session_summary()

        assert "SESSION DONE" in caplog.text
        assert "1 completed" in caplog.text
        assert "1 failed" in caplog.text
        assert "1 skipped" in caplog.text

    def test_log_slot_start(self, caplog):
        t = StepTracker("sess-12345678", "sequential")
        t.register_names({"s1": "Architect"})

        with caplog.at_level(logging.INFO):
            t.log_slot_start("STEP 1 · Architect", "s1", role="Design architecture", context="max_turns=5")

        assert "[ORCH s:sess-123" in caplog.text
        assert "starting" in caplog.text
        assert "Design architecture" in caplog.text

    def test_log_handoff(self, caplog):
        t = StepTracker("sess-12345678", "sequential")
        t.register_names({"s1": "Architect", "s2": "Modeler"})
        t.next_step()  # step 1

        with caplog.at_level(logging.INFO):
            t.log_handoff("s1", "s2", "report")

        assert "handoff" in caplog.text
        assert "Architect" in caplog.text
        assert "Modeler" in caplog.text

    def test_log_parallel_start_and_done(self, caplog):
        t = StepTracker("sess-12345678", "supervisor")
        t.register_names({"w1": "Worker1", "w2": "Worker2"})
        t.next_step()  # step 1

        with caplog.at_level(logging.INFO):
            t.log_parallel_start(["w1", "w2"])
            t.log_parallel_done(2, 0)

        assert "2 agents in parallel" in caplog.text
        assert "Worker1" in caplog.text
        assert "2 succeeded, 0 failed" in caplog.text

    def test_step_labels_for_full_sequential_flow(self):
        """Simulates a 4-slot sequential pipeline and verifies numbering."""
        t = StepTracker("sess-ddafec94", "sequential")
        t.register_names({
            "slot-1": "Architect",
            "slot-2": "Modeler",
            "slot-3": "FabricAdmin",
            "slot-4": "FabricDataEngineer",
        })

        assert t.next_step() == 1
        assert t.seq_label("slot-1") == "STEP 1 · Architect"
        assert t.next_step() == 2
        assert t.seq_label("slot-2") == "STEP 2 · Modeler"
        assert t.next_step() == 3
        assert t.seq_label("slot-3") == "STEP 3 · FabricAdmin"
        assert t.next_step() == 4
        assert t.seq_label("slot-4") == "STEP 4 · FabricDataEngineer"

    def test_step_labels_for_supervisor_flow(self):
        """Simulates lead → 2 parallel workers → lead synthesis."""
        t = StepTracker("sess-abc12345", "supervisor")
        t.register_names({
            "lead": "Orchestrator",
            "w1": "FabricAdmin",
            "w2": "FDE",
        })

        # Step 1: Lead planning
        assert t.next_step() == 1
        assert t.seq_label("lead") == "STEP 1 · Orchestrator"

        # Step 2: Workers in parallel
        assert t.next_step() == 2
        labels = t.parallel_labels(["w1", "w2"])
        assert labels["w1"] == "STEP 2-A · FabricAdmin"
        assert labels["w2"] == "STEP 2-B · FDE"

        # Step 3: Lead synthesis
        assert t.next_step() == 3
        assert t.seq_label("lead") == "STEP 3 · Orchestrator"

    def test_step_labels_for_reflection_flow(self):
        """Simulates actor→critic→actor→critic→tester."""
        t = StepTracker("sess-ref12345", "reflection")
        t.register_names({
            "actor": "FDE (actor)",
            "critic": "FDE (critic)",
            "tester": "FDE (tester)",
        })

        # Iteration 1
        assert t.next_step() == 1
        assert t.seq_label("actor") == "STEP 1 · FDE (actor)"
        assert t.next_step() == 2
        assert t.seq_label("critic") == "STEP 2 · FDE (critic)"

        # Iteration 2
        assert t.next_step() == 3
        assert t.seq_label("actor") == "STEP 3 · FDE (actor)"
        assert t.next_step() == 4
        assert t.seq_label("critic") == "STEP 4 · FDE (critic)"

        # Tester
        assert t.next_step() == 5
        assert t.seq_label("tester") == "STEP 5 · FDE (tester)"
