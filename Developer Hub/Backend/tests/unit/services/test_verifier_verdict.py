import json
from datetime import UTC, datetime

from domain.models.dynamic_orchestration import (
    AgentResult,
    AgentResultStatus,
    FollowupTask,
    MissionBrief,
    MissionState,
    SubagentRun,
    TaskNode,
)
from services.agenthub.verifier_verdict import (
    compute_structural_rubric,
    emit_verifier_verdict,
    synthesize_mandatory_verification_followup,
)


def test_structural_rubric_uses_scheduled_deliverables_and_browser_action_details() -> None:
    producer_task = TaskNode(id="task-producer", title="Create", objective="Create report")
    verifier_task = TaskNode(
        id="task-verifier",
        title="Verify",
        objective="Verify report",
        parent_task_id=producer_task.id,
    )
    report = {
        "id": "report-123",
        "type": "Report",
        "name": "Inventory Report",
        "webUrl": "https://app.powerbi.com/groups/w/reports/report-123",
    }
    state = MissionState(
        brief=MissionBrief(session_id="s", goal="make a report", workspace_id="w"),
        tasks={producer_task.id: producer_task, verifier_task.id: verifier_task},
        blackboard={
            "mandatoryVerifierScheduledForTasks": {
                producer_task.id: {"deliverables": [report]},
            }
        },
    )
    result = AgentResult(
        run_id="run-1",
        task_id=verifier_task.id,
        status=AgentResultStatus.SUCCESS,
        summary="verified",
        evidence=[
            {
                "stepResults": [
                    {"step": "Professional quality review", "status": "passed", "evidence": "multi-visual report, naming convention fit, modern style/theme, information hierarchy, championship 3-30-300 story flow with top-left overview, filter and zoom usability interactions, details on demand, methodology/source transparency, accessibility alt text contrast tab order, clean code, classes/functions, maintainable data path, extensibility, and explicit error handling"},
                ]
            }
        ],
        artifacts=[
            {
                "entityType": "browser_visual_render",
                "entityName": report["webUrl"],
                "webUrl": report["webUrl"],
                "details": json.dumps(
                    {
                        "ok": True,
                        "status": "passed",
                        "url": report["webUrl"],
                        "finalUrl": report["webUrl"],
                        "screenshotPath": "/tmp/report.png",
                        "bodyTextSample": "Power BI report",
                        "visualSummary": {"visibleVisualLikeElementCount": 2},
                    }
                ),
            }
        ],
    )

    passed, failures, evidence, deliverables = compute_structural_rubric(state, verifier_task, result)

    assert passed is True
    assert failures == []
    assert deliverables == [report]
    assert evidence["browserVerifiedUrls"] == [report["webUrl"]]
    assert evidence["screenshotPaths"] == ["/tmp/report.png"]
    assert evidence["visualsRendered"] is True


def test_structural_rubric_requires_browser_screenshot_for_report_design_review() -> None:
    producer_task = TaskNode(id="task-producer", title="Create", objective="Create report")
    verifier_task = TaskNode(
        id="task-verifier",
        title="Verify",
        objective="Verify report",
        parent_task_id=producer_task.id,
    )
    report = {
        "id": "report-123",
        "type": "Report",
        "name": "Inventory Report",
        "webUrl": "https://app.powerbi.com/groups/w/reports/report-123",
    }
    state = MissionState(
        brief=MissionBrief(session_id="s", goal="make a report", workspace_id="w"),
        tasks={producer_task.id: producer_task, verifier_task.id: verifier_task},
        blackboard={"mandatoryVerifierScheduledForTasks": {producer_task.id: {"deliverables": [report]}}},
    )
    result = AgentResult(
        run_id="run-1",
        task_id=verifier_task.id,
        status=AgentResultStatus.SUCCESS,
        summary="verified",
        evidence=[
            {
                "stepResults": [
                    {"step": "Professional quality review", "status": "passed", "evidence": "multi-visual report, naming convention fit, modern style/theme, information hierarchy, championship 3-30-300 story flow with top-left overview, filter and zoom usability interactions, details on demand, methodology/source transparency, accessibility alt text contrast tab order, clean code, classes/functions, maintainable data path, extensibility, and explicit error handling"},
                ]
            },
            {
                "toolName": "browser_verify_visual_render",
                "url": report["webUrl"],
                "finalUrl": report["webUrl"],
                "bodyTextSample": "Power BI report canvas",
                "visualLikeElementCount": 4,
                "status": "passed",
            },
        ],
    )

    passed, failures, evidence, _deliverables = compute_structural_rubric(state, verifier_task, result)

    assert passed is False
    assert "NO_BROWSER_SCREENSHOT_EVIDENCE" in failures
    assert evidence["browserVerifiedUrls"] == [report["webUrl"]]
    assert evidence["screenshotPaths"] == []


def test_mandatory_verification_followup_clips_long_objective() -> None:
    deliverables = [
        {
            "type": "Report",
            "name": f"Report {idx}",
            "id": f"report-{idx}",
            "webUrl": "https://app.powerbi.com/groups/w/reports/" + ("x" * 200),
        }
        for idx in range(30)
    ]

    followup = synthesize_mandatory_verification_followup(
        deliverables,
        original_goal="Create a complete Fabric inventory solution. " * 200,
    )

    assert len(followup.objective) <= 4000
    assert followup.candidate_agent_ids == ["fabric-verifier"]


def test_verdict_accepts_visual_render_when_expected_text_followup_is_only_issue() -> None:
    verifier_task = TaskNode(
        id="task-verifier",
        title="Verify",
        objective="Verify report",
        parent_task_id="generalist",
    )
    state = MissionState(
        brief=MissionBrief(session_id="s", goal="make a report", workspace_id="w"),
        tasks={verifier_task.id: verifier_task},
    )
    report_url = "https://app.powerbi.com/groups/w/reports/report-123"
    result = AgentResult(
        run_id="run-1",
        task_id=verifier_task.id,
        status=AgentResultStatus.SUCCESS,
        summary="Browser render passed; text was not scrapeable.",
        evidence=[
            {
                "stepResults": [
                    {"step": "Professional quality review", "status": "passed", "evidence": "visual design, naming convention fit, modern style/theme, information hierarchy, championship 3-30-300 story flow, usability interactions/tooltips, methodology/source transparency, accessibility alt text contrast keyboard tab order, semantic model quality, generated-code classes/functions, maintainability, extensibility, and error handling reviewed"},
                ]
            }
        ],
        artifacts=[
            {
                "entityType": "browser_visual_render",
                "entityName": report_url,
                "webUrl": report_url,
                "details": json.dumps(
                    {
                        "ok": True,
                        "status": "passed",
                        "finalUrl": report_url,
                        "screenshotPath": "/tmp/report.png",
                        "bodyTextSample": "Power BI report canvas",
                        "expectedTextMatched": False,
                        "visualSummary": {"visibleVisualLikeElementCount": 3},
                    }
                ),
            }
        ],
        followup_tasks=[
            FollowupTask(
                title="Repair scrapeable expected text",
                objective="The visual rendered, but text was not visible in DOM.",
                candidate_agent_ids=["fabric-data-engineer"],
            )
        ],
    )
    captured: list[dict] = []

    emit_verifier_verdict(
        state=state,
        task=verifier_task,
        run=SubagentRun(id="run-1", task_id=verifier_task.id, agent_id="fabric-verifier"),
        result=result,
        emit=lambda event, _state, **payload: captured.append({"event": event, **payload}),
        now=lambda: datetime(2026, 1, 1, tzinfo=UTC),
        root_task_id_fn=lambda _state, task: task.parent_task_id or task.id,
    )

    assert captured[0]["event"] == "verifier_verdict"
    assert captured[0]["passed"] is True
    assert captured[0]["requiresUserBrowserRender"] is True
    assert captured[0]["deliverables"][0]["type"] == "Report"
    assert captured[0]["evidence"]["expectedTextMatched"] is False
    assert captured[0]["evidence"]["visualsRendered"] is True


def test_structural_rubric_treats_stale_loading_text_as_failure_even_with_visual_capture() -> None:
    verifier_task = TaskNode(
        id="task-verifier",
        title="Verify",
        objective="Verify report",
        parent_task_id="producer",
    )
    report = {
        "id": "report-123",
        "type": "Report",
        "name": "Inventory Report",
        "webUrl": "https://app.powerbi.com/groups/w/reports/report-123",
    }
    state = MissionState(
        brief=MissionBrief(session_id="s", goal="make a report", workspace_id="w"),
        tasks={verifier_task.id: verifier_task},
        blackboard={"mandatoryVerifierScheduledForTasks": {"producer": {"deliverables": [report]}}},
    )
    result = AgentResult(
        run_id="run-1",
        task_id=verifier_task.id,
        status=AgentResultStatus.SUCCESS,
        summary="browser render captured but stuck on loading",
        artifacts=[
            {
                "entityType": "browser_visual_render",
                "entityName": report["webUrl"],
                "webUrl": report["webUrl"],
                "details": json.dumps(
                    {
                        "ok": True,
                        "status": "passed",
                        "finalUrl": report["webUrl"],
                        "screenshotPath": "/tmp/report.png",
                        "bodyTextSample": "Power BI Report Loading your report... Report Zoomed To 100%",
                        "visualSummary": {"visibleVisualLikeElementCount": 8},
                    }
                ),
            }
        ],
    )

    passed, failures, evidence, _deliverables = compute_structural_rubric(state, verifier_task, result)

    assert passed is False
    assert "REPORT_STUCK_LOADING" in failures
    assert evidence["loadingStuckObserved"] is True
    assert evidence["visualsRendered"] is True


def test_structural_rubric_requires_professional_quality_review_for_report() -> None:
    verifier_task = TaskNode(
        id="task-verifier",
        title="Verify",
        objective="Verify report",
        parent_task_id="producer",
    )
    report = {
        "id": "report-123",
        "type": "Report",
        "name": "Inventory Report",
        "webUrl": "https://app.powerbi.com/groups/w/reports/report-123",
    }
    state = MissionState(
        brief=MissionBrief(session_id="s", goal="make a report", workspace_id="w"),
        tasks={verifier_task.id: verifier_task},
        blackboard={"mandatoryVerifierScheduledForTasks": {"producer": {"deliverables": [report]}}},
    )
    result = AgentResult(
        run_id="run-1",
        task_id=verifier_task.id,
        status=AgentResultStatus.SUCCESS,
        summary="browser render captured",
        artifacts=[
            {
                "entityType": "browser_visual_render",
                "entityName": report["webUrl"],
                "webUrl": report["webUrl"],
                "details": json.dumps(
                    {
                        "ok": True,
                        "status": "passed",
                        "finalUrl": report["webUrl"],
                        "screenshotPath": "/tmp/report.png",
                        "bodyTextSample": "Power BI Report",
                        "visualSummary": {"visibleVisualLikeElementCount": 8},
                    }
                ),
            }
        ],
    )

    passed, failures, _evidence, _deliverables = compute_structural_rubric(state, verifier_task, result)

    assert passed is False
    assert "NO_PROFESSIONAL_QUALITY_REVIEW" in failures


def test_structural_rubric_requires_naming_and_style_in_quality_review() -> None:
    verifier_task = TaskNode(
        id="task-verifier",
        title="Verify",
        objective="Verify report",
        parent_task_id="producer",
    )
    report = {
        "id": "report-123",
        "type": "Report",
        "name": "Inventory Report",
        "webUrl": "https://app.powerbi.com/groups/w/reports/report-123",
    }
    state = MissionState(
        brief=MissionBrief(session_id="s", goal="make a report", workspace_id="w"),
        tasks={verifier_task.id: verifier_task},
        blackboard={"mandatoryVerifierScheduledForTasks": {"producer": {"deliverables": [report]}}},
    )
    result = AgentResult(
        run_id="run-1",
        task_id=verifier_task.id,
        status=AgentResultStatus.SUCCESS,
        summary="browser render captured",
        evidence=[
            {
                "stepResults": [
                    {"step": "Professional quality review", "status": "passed", "evidence": "report design and semantic model quality reviewed"},
                ]
            }
        ],
        artifacts=[
            {
                "entityType": "browser_visual_render",
                "entityName": report["webUrl"],
                "webUrl": report["webUrl"],
                "details": json.dumps(
                    {
                        "ok": True,
                        "status": "passed",
                        "finalUrl": report["webUrl"],
                        "screenshotPath": "/tmp/report.png",
                        "bodyTextSample": "Power BI Report",
                        "visualSummary": {"visibleVisualLikeElementCount": 8},
                    }
                ),
            }
        ],
    )

    passed, failures, _evidence, _deliverables = compute_structural_rubric(state, verifier_task, result)

    assert passed is False
    assert "QUALITY_REVIEW_MISSING:NAMING_CONVENTION" in failures
    assert "QUALITY_REVIEW_MISSING:STYLE_THEME" in failures
    assert "QUALITY_REVIEW_MISSING:CHAMPIONSHIP_STORYTELLING" in failures
    assert "QUALITY_REVIEW_MISSING:ACCESSIBILITY" in failures
    assert "QUALITY_REVIEW_MISSING:SOFTWARE_ENGINEERING" in failures


def test_structural_rubric_accepts_structured_producer_quality_for_naming_and_style() -> None:
    producer_task = TaskNode(id="task-producer", title="Create", objective="Create solution")
    verifier_task = TaskNode(
        id="task-verifier",
        title="Verify",
        objective="Verify report",
        parent_task_id=producer_task.id,
    )
    report = {
        "id": "report-123",
        "type": "Report",
        "name": "Fabric Items Inventory Report",
        "webUrl": "https://app.powerbi.com/groups/w/reports/report-123",
    }
    state = MissionState(
        brief=MissionBrief(session_id="s", goal="make a report", workspace_id="w"),
        tasks={producer_task.id: producer_task, verifier_task.id: verifier_task},
        blackboard={
            "mandatoryVerifierScheduledForTasks": {producer_task.id: {"deliverables": [report]}},
            "taskResults": {
                producer_task.id: {
                    "artifacts": [
                        {
                            "kind": "WorkspaceInventorySolution",
                            "details": json.dumps(
                                {
                                    "status": "created",
                                    "namingConvention": {
                                        "status": "defaulted",
                                        "preferredStyle": "title_case_spaces",
                                    },
                                    "qualityValidation": {
                                        "status": "passed",
                                        "report": {
                                            "status": "passed",
                                            "storyFlow3_30_300": True,
                                            "informationHierarchy": True,
                                            "usabilityInteractions": True,
                                            "methodologyTransparency": True,
                                            "accessibilityMetadata": True,
                                            "guidedTabOrder": True,
                                            "highContrastCanvas": True,
                                            "checks": [
                                                {"name": "report_has_modern_reader_experience", "passed": True},
                                                {"name": "report_has_modern_theme", "passed": True},
                                                {"name": "report_follows_3_30_300_story_flow", "passed": True},
                                                {"name": "report_has_clear_information_hierarchy", "passed": True},
                                                {"name": "report_has_usability_interactions", "passed": True},
                                                {"name": "report_has_methodology_transparency", "passed": True},
                                                {"name": "report_has_accessibility_alt_text_and_titles", "passed": True},
                                                {"name": "report_has_guided_keyboard_tab_order", "passed": True},
                                                {"name": "report_has_high_contrast_canvas", "passed": True},
                                            ],
                                        },
                                        "notebookCode": {
                                            "status": "passed",
                                            "checks": [
                                                {"name": "code_uses_config_class", "passed": True},
                                                {"name": "code_uses_api_client_class", "passed": True},
                                                {"name": "code_uses_writer_class", "passed": True},
                                                {"name": "code_raises_explicit_runtime_errors", "passed": True},
                                                {"name": "code_tracks_partial_workspace_warnings", "passed": True},
                                            ],
                                        },
                                    },
                                    "createdItems": [report],
                                }
                            ),
                        }
                    ],
                    "evidence": [],
                }
            },
        },
    )
    result = AgentResult(
        run_id="run-1",
        task_id=verifier_task.id,
        status=AgentResultStatus.SUCCESS,
        summary="browser render captured",
        evidence=[
            {
                "stepResults": [
                    {"step": "Professional quality review", "status": "passed", "evidence": "report design and semantic model quality reviewed"},
                ]
            }
        ],
        artifacts=[
            {
                "entityType": "browser_visual_render",
                "entityName": report["webUrl"],
                "webUrl": report["webUrl"],
                "details": json.dumps(
                    {
                        "ok": True,
                        "status": "passed",
                        "finalUrl": report["webUrl"],
                        "screenshotPath": "/tmp/report.png",
                        "bodyTextSample": "Power BI Report",
                        "visualSummary": {"visibleVisualLikeElementCount": 8},
                    }
                ),
            }
        ],
    )

    passed, failures, _evidence, _deliverables = compute_structural_rubric(state, verifier_task, result)

    assert passed is True
    assert failures == []


def test_structural_rubric_inherits_producer_quality_through_verifier_followup_chain() -> None:
    producer_task = TaskNode(id="task-producer", title="Create", objective="Create solution")
    first_verifier_task = TaskNode(
        id="task-verifier-1",
        title="Verify",
        objective="Verify report",
        parent_task_id=producer_task.id,
    )
    followup_verifier_task = TaskNode(
        id="task-verifier-2",
        title="Re-verify",
        objective="Re-verify report with browser evidence",
        parent_task_id=first_verifier_task.id,
    )
    report = {
        "id": "report-123",
        "type": "Report",
        "name": "Fabric Items Inventory Report",
        "webUrl": "https://app.powerbi.com/groups/w/reports/report-123",
    }
    state = MissionState(
        brief=MissionBrief(session_id="s", goal="make a report", workspace_id="w"),
        tasks={
            producer_task.id: producer_task,
            first_verifier_task.id: first_verifier_task,
            followup_verifier_task.id: followup_verifier_task,
        },
        blackboard={
            "mandatoryVerifierScheduledForTasks": {producer_task.id: {"deliverables": [report]}},
            "taskResults": {
                producer_task.id: {
                    "artifacts": [
                        {
                            "kind": "WorkspaceInventorySolution",
                            "details": json.dumps(
                                {
                                    "status": "created",
                                    "folderName": "tmp_20260429183115",
                                    "namingConvention": {"status": "passed"},
                                    "qualityValidation": {
                                        "status": "passed",
                                        "report": {
                                            "status": "passed",
                                            "storyFlow3_30_300": True,
                                            "informationHierarchy": True,
                                            "usabilityInteractions": True,
                                            "methodologyTransparency": True,
                                            "accessibilityMetadata": True,
                                            "guidedTabOrder": True,
                                            "highContrastCanvas": True,
                                            "checks": [
                                                {"name": "report_has_modern_theme", "passed": True},
                                                {"name": "report_has_visual_style_defaults", "passed": True},
                                                {"name": "report_follows_3_30_300_story_flow", "passed": True},
                                                {"name": "report_has_clear_information_hierarchy", "passed": True},
                                                {"name": "report_has_usability_interactions", "passed": True},
                                                {"name": "report_has_methodology_transparency", "passed": True},
                                                {"name": "report_has_accessibility_alt_text_and_titles", "passed": True},
                                                {"name": "report_has_guided_keyboard_tab_order", "passed": True},
                                                {"name": "report_has_high_contrast_canvas", "passed": True},
                                            ],
                                        },
                                        "notebookCode": {
                                            "status": "passed",
                                            "checks": [
                                                {"name": "code_uses_config_class", "passed": True},
                                                {"name": "code_has_clear_function_boundaries", "passed": True},
                                                {"name": "code_raises_explicit_runtime_errors", "passed": True},
                                            ],
                                        },
                                    },
                                    "createdItems": [report],
                                }
                            ),
                        }
                    ],
                    "evidence": [],
                }
            },
        },
    )
    result = AgentResult(
        run_id="run-2",
        task_id=followup_verifier_task.id,
        status=AgentResultStatus.SUCCESS,
        summary="browser render captured",
        evidence=[
            {
                "toolName": "browser_verify_visual_render",
                "url": report["webUrl"],
                "finalUrl": report["webUrl"],
                "screenshotPath": "/tmp/report.png",
                "bodyTextSample": "Power BI report canvas",
                "visualLikeElementCount": 8,
            }
        ],
    )

    passed, failures, evidence, deliverables = compute_structural_rubric(state, followup_verifier_task, result)

    assert passed is True
    assert failures == []
    assert evidence["visualsRendered"] is True
    assert deliverables[0]["type"] == "Report"


def test_structural_rubric_finds_report_from_sibling_inventory_solution_result() -> None:
    root_task = TaskNode(id="generalist", title="Plan", objective="Create inventory report")
    producer_task = TaskNode(
        id="fabric-producer",
        title="Create Fabric inventory solution",
        objective="Create folder, lakehouse, semantic model, and report",
        parent_task_id=root_task.id,
    )
    verifier_task = TaskNode(
        id="fabric-verifier",
        title="Verify Fabric inventory solution",
        objective="Verify the produced Fabric solution",
        parent_task_id=root_task.id,
        dependencies=[producer_task.id],
    )
    report = {
        "id": "report-123",
        "type": "Report",
        "displayName": "Fabric Items Inventory Report",
        "webUrl": "https://app.powerbi.com/groups/w/reports/report-123",
    }
    state = MissionState(
        brief=MissionBrief(session_id="s", goal="make a report", workspace_id="w"),
        tasks={
            root_task.id: root_task,
            producer_task.id: producer_task,
            verifier_task.id: verifier_task,
        },
        blackboard={
            "taskResults": {
                producer_task.id: {
                    "artifacts": [
                        {
                            "entityType": "WorkspaceInventorySolution",
                            "entityName": "tmp_20260501132233",
                            "fabricItemId": "folder-123",
                            "details": json.dumps(
                                {
                                    "status": "created",
                                    "folderName": "tmp_20260501132233",
                                    "createdItems": [report],
                                    "qualityValidation": {
                                        "status": "passed",
                                        "report": {
                                            "status": "passed",
                                            "storyFlow3_30_300": True,
                                            "informationHierarchy": True,
                                            "usabilityInteractions": True,
                                            "methodologyTransparency": True,
                                            "accessibilityMetadata": True,
                                            "guidedTabOrder": True,
                                            "highContrastCanvas": True,
                                        },
                                        "notebookCode": {"status": "passed"},
                                    },
                                }
                            ),
                        }
                    ],
                    "evidence": [],
                }
            }
        },
    )
    result = AgentResult(
        run_id="run-1",
        task_id=verifier_task.id,
        status=AgentResultStatus.SUCCESS,
        summary="The workspace inventory solution is verified by server-side checks.",
        artifacts=[
            {
                "entityType": "WorkspaceInventorySolution",
                "entityName": "tmp_20260501132233",
                "fabricItemId": "folder-123",
                "details": json.dumps({"status": "verified", "items": [report]}),
            }
        ],
    )

    passed, failures, evidence, deliverables = compute_structural_rubric(state, verifier_task, result)

    assert passed is False
    assert "NO_USER_BROWSER_EVIDENCE" in failures
    assert any(deliverable["type"] == "Report" for deliverable in deliverables)
    assert evidence["browserVerifiedUrls"] == []


def test_verdict_extracts_per_step_results_and_flags_failed_step() -> None:
    """The verifier breaks the goal into discrete steps and reports each
    step's pass/fail. The verdict must surface that breakdown so the UI
    can show the user *which* phase broke (e.g. the semantic model is
    not queryable) instead of an opaque single-status verdict.
    """
    producer_task = TaskNode(id="task-producer", title="Create", objective="Create solution")
    verifier_task = TaskNode(
        id="task-verifier",
        title="Verify",
        objective="Verify solution",
        parent_task_id=producer_task.id,
    )
    report = {
        "id": "report-1",
        "type": "Report",
        "name": "Inventory Report",
        "webUrl": "https://app.powerbi.com/groups/w/reports/report-1",
    }
    state = MissionState(
        brief=MissionBrief(session_id="s", goal="end-to-end inventory", workspace_id="w"),
        tasks={producer_task.id: producer_task, verifier_task.id: verifier_task},
        blackboard={"mandatoryVerifierScheduledForTasks": {producer_task.id: {"deliverables": [report]}}},
    )
    result = AgentResult(
        run_id="run-1",
        task_id=verifier_task.id,
        status=AgentResultStatus.SUCCESS,
        summary="Decomposed goal; semantic model query failed.",
        evidence=[
            {
                "stepResults": [
                    {"step": "Data ingestion", "status": "passed", "evidence": "2 Delta tables present"},
                    {"step": "Notebook transformation", "status": "passed", "exitValue": "ok"},
                    {
                        "step": "Semantic model queryable",
                        "status": "failed",
                        "reason": "DAX returned Invalid object name 'dbo.X_FabricItems'",
                        "via": "powerbi_executeQueries",
                    },
                    {"step": "Report renders", "status": "not_applicable", "detail": "blocked by upstream step"},
                ]
            },
            {
                "toolName": "browser_verify_visual_render",
                "url": report["webUrl"],
                "finalUrl": report["webUrl"],
                "screenshotPath": "/tmp/report.png",
                "bodyTextSample": "Power BI canvas",
                "visualLikeElementCount": 0,
            },
        ],
    )
    captured: list[dict] = []

    emit_verifier_verdict(
        state=state,
        task=verifier_task,
        run=SubagentRun(id="run-1", task_id=verifier_task.id, agent_id="fabric-verifier"),
        result=result,
        emit=lambda event, _state, **payload: captured.append({"event": event, **payload}),
        now=lambda: datetime(2026, 1, 1, tzinfo=UTC),
        root_task_id_fn=lambda _state, task: task.parent_task_id or task.id,
    )

    verdict = captured[0]
    assert verdict["event"] == "verifier_verdict"
    assert verdict["passed"] is False
    step_results = verdict["stepResults"]
    assert {row["step"] for row in step_results} == {
        "Data ingestion",
        "Notebook transformation",
        "Semantic model queryable",
        "Report renders",
    }
    failed_step = next(row for row in step_results if row["status"] == "failed")
    assert failed_step["step"] == "Semantic model queryable"
    assert "Invalid object name" in failed_step["reason"]
    # The failed step appears in structuralFailures with a stable tag so
    # the orchestrator/UI can map it back to a remediation hint.
    assert any(
        f.startswith("STEP_FAILED:SEMANTIC_MODEL_QUERYABLE")
        for f in verdict["structuralFailures"]
    )
