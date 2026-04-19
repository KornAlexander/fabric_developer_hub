import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FluentProvider, webLightTheme } from "@fluentui/react-components";
import { PlanView } from "../../src/components/AgentHub/plan/PlanView";
import type { Plan } from "../../src/components/AgentHub/plan/types";

function wrap(ui: React.ReactElement): React.ReactElement {
    return <FluentProvider theme={webLightTheme}>{ui}</FluentProvider>;
}

function makePlan(overrides: Partial<Plan> = {}): Plan {
    return {
        jobId: "j-1",
        summary: "Create a bronze lakehouse in the destination workspace.",
        assumptions: [],
        prerequisites: [],
        steps: [
            {
                id: "s1",
                order: 1,
                title: "Create lh_bronze",
                action: "create",
                target: {
                    itemType: "Lakehouse",
                    displayName: "lh_bronze",
                    workspaceId: "ws-1",
                },
                inputs: [],
                dependsOn: [],
                rationale: "Destination has no bronze lakehouse yet.",
                risk: "low",
                reversible: true,
            },
        ],
        workspaceItems: [],
        noAction: [],
        conflicts: [],
        clarificationsNeeded: [],
        footer: {
            agentCount: 1,
            stepCount: 1,
            approvalPoints: 0,
            executionBlocked: false,
        },
        ...overrides,
    };
}

describe("PlanView", () => {
    it("renders summary, steps, and action buttons for an actionable plan", () => {
        render(
            wrap(
                <PlanView
                    plan={makePlan()}
                    workspaceName="Dest WS"
                    approving={false}
                    onApprove={vi.fn()}
                    onReject={vi.fn()}
                />
            )
        );

        expect(screen.getByTestId("plan")).toBeInTheDocument();
        expect(screen.getByTestId("plan-steps")).toBeInTheDocument();
        expect(screen.getByText(/Create lh_bronze/)).toBeInTheDocument();
        expect(screen.getByTestId("plan-approve-btn")).toBeEnabled();
        expect(screen.getByTestId("plan-reject-btn")).toBeEnabled();
    });

    it("disables Approve when conflicts are present", () => {
        const plan = makePlan({
            conflicts: [
                {
                    itemType: "Lakehouse",
                    displayName: "payload",
                    description: "Name collides with an existing Notebook.",
                    resolutionOptions: ["Rename", "Replace"],
                },
            ],
        });
        render(
            wrap(
                <PlanView
                    plan={plan}
                    workspaceName="Dest WS"
                    approving={false}
                    onApprove={vi.fn()}
                    onReject={vi.fn()}
                />
            )
        );

        expect(screen.getByTestId("plan-conflicts")).toBeInTheDocument();
        expect(screen.getByTestId("plan-approve-btn")).toBeDisabled();
    });

    it("disables Approve when footer.executionBlocked is true (spec §4)", () => {
        const plan = makePlan({
            footer: {
                agentCount: 1,
                stepCount: 1,
                approvalPoints: 0,
                executionBlocked: true,
            },
        });
        render(
            wrap(
                <PlanView
                    plan={plan}
                    workspaceName="Dest WS"
                    approving={false}
                    onApprove={vi.fn()}
                    onReject={vi.fn()}
                />
            )
        );
        expect(screen.getByTestId("plan-approve-btn")).toBeDisabled();
    });

    it("shows prerequisite rows with Recheck button when a prereq is missing", () => {
        const plan = makePlan({
            prerequisites: [
                {
                    id: "p1",
                    title: "Member role on destination workspace",
                    description: "User must be a Member.",
                    status: "missing",
                    evidence: "User not in Member role",
                    text: "Member role on destination workspace",
                    category: "workspace_role",
                    appliesToStepIds: ["s1"],
                    verification: {
                        kind: "fabric_api",
                        spec: {},
                        status: "missing",
                        evidence: "User not in Member role",
                    },
                },
            ],
            footer: {
                agentCount: 1,
                stepCount: 1,
                approvalPoints: 0,
                executionBlocked: true,
            },
        });
        render(
            wrap(
                <PlanView
                    plan={plan}
                    workspaceName="Dest WS"
                    approving={false}
                    onApprove={vi.fn()}
                    onReject={vi.fn()}
                />
            )
        );
        expect(screen.getByTestId("plan-prereq-p1")).toBeInTheDocument();
        expect(screen.getByTestId("plan-prereq-recheck-p1")).toBeInTheDocument();
        expect(screen.getByTestId("plan-approve-btn")).toBeDisabled();
    });

    it("renders unknown-notice banner when any prereq is unknown", () => {
        const plan = makePlan({
            prerequisites: [
                {
                    id: "p1",
                    title: "Capacity assigned",
                    description: "Must be on an active capacity.",
                    status: "unknown",
                    text: "Capacity assigned",
                    category: "capacity",
                    appliesToStepIds: ["s1"],
                    verification: {
                        kind: "capacity_api",
                        spec: {},
                        status: "unknown",
                        unknownReason: "probe not implemented",
                    },
                },
            ],
        });
        render(
            wrap(
                <PlanView
                    plan={plan}
                    workspaceName="Dest WS"
                    approving={false}
                    onApprove={vi.fn()}
                    onReject={vi.fn()}
                />
            )
        );
        expect(
            screen.getByTestId("plan-prereqs-unknown-notice"),
        ).toBeInTheDocument();
    });

    it("fires onApprove when the Approve button is clicked", async () => {
        const onApprove = vi.fn();
        render(
            wrap(
                <PlanView
                    plan={makePlan()}
                    workspaceName="Dest WS"
                    approving={false}
                    onApprove={onApprove}
                    onReject={vi.fn()}
                />
            )
        );
        await userEvent.click(screen.getByTestId("plan-approve-btn"));
        expect(onApprove).toHaveBeenCalledTimes(1);
    });

    it("renders workspace_items with keep_as_is and will_be_changed groups", async () => {
        const plan = makePlan({
            workspaceItems: [
                {
                    item: "Pipeline_1",
                    type: "Pipeline",
                    disposition: "keep_as_is",
                    reason: "Already scheduling the upstream ingestion.",
                },
                {
                    item: "sm_old",
                    type: "SemanticModel",
                    disposition: "will_be_changed",
                    reason: "Extended with DirectLake tables.",
                    drivenByStepId: "s1",
                },
            ],
        });
        render(
            wrap(
                <PlanView
                    plan={plan}
                    workspaceName="Dest WS"
                    approving={false}
                    onApprove={vi.fn()}
                    onReject={vi.fn()}
                />
            )
        );
        // Section is collapsed by default — click to expand. Test
        // harness doesn't boot i18n, so labels come through as literal
        // translation keys.
        await userEvent.click(screen.getByText("Plan_WorkspaceItems_Title"));
        const group = screen.getByTestId("plan-workspace-items");
        expect(group).toBeInTheDocument();
        expect(group.textContent).toContain("Pipeline_1");
        expect(group.textContent).toContain("sm_old");
    });
});
