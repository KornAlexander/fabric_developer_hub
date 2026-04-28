import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import {
    Card,
} from "@fluentui/react-components";
import {
    ShieldCheckmark20Regular,
    Warning20Regular,
    ChevronDown16Regular,
    ChevronRight16Regular,
    Checkmark16Regular,
    Dismiss16Regular,
    ArrowClockwise16Regular,
    Edit16Regular,
} from "@fluentui/react-icons";
import type {
    BlastRadius,
    PlanStep,
    RecoveryAction,
    ToolCallPreview,
} from "../plan/types";

/**
 * Inline approval card shown in the session's right pane when a
 * running step emits ``approval.requested``. Presents plain-language
 * summary, reversibility badge, blast-radius chip, collapsible
 * tool-call preview, and up to four action buttons.
 *
 * Per IMPLEMENTATION.md §P4: approvals are NOT dismissible via Esc —
 * the user must pick an action. Primary CTA uses Fluent's
 * ``appearance="primary"`` rather than reintroducing Tailwind.
 */

interface ApprovalCardProps {
    step: PlanStep;
    // Optional overrides for cases where the approval request is not
    // derived directly from a PlanStep (e.g. mid-run tool-call approval
    // that wasn't known at plan time).
    title?: string;
    summary?: string;
    reversible?: boolean;
    blastRadius?: BlastRadius | null;
    toolCallPreview?: ToolCallPreview | null;
    recoveryActions?: RecoveryAction[];
    busy?: boolean;
    onAction: (action: RecoveryAction) => void;
}

const DEFAULT_ACTIONS: RecoveryAction[] = [
    "approve",
    "decline",
    "request_alternative",
    "edit_input",
];

function approvalVerb(step: PlanStep, summary?: string, preview?: ToolCallPreview | null): string {
    const haystack = `${summary || ""} ${step.title || ""} ${step.rationale || ""} ${preview?.name || ""}`.toLowerCase();
    if (haystack.includes("certif")) return "Approve & certify";
    if (haystack.includes("publish")) return "Approve & publish";
    if (haystack.includes("delete") || haystack.includes("abandon")) return "Approve action";
    return "Approve";
}

function alternateLabel(action: RecoveryAction, summary?: string, preview?: ToolCallPreview | null): string {
    const haystack = `${summary || ""} ${preview?.name || ""}`.toLowerCase();
    if (action === "decline") return haystack.includes("certif") ? "Abandon run" : "Decline";
    if (action === "request_alternative") return haystack.includes("tolerance") || haystack.includes("certif") ? "Tighten tolerance" : "Request alternative";
    if (action === "edit_input") return "Swap agent";
    return "Approve";
}

function blastCopy(blast: BlastRadius | null): string {
    switch (blast) {
        case "workspace": return "Workspace-wide changes may be visible to other users.";
        case "item": return "This changes one Fabric item and its direct consumers.";
        case "row-level": return "No row-level writes.";
        case "metadata-only": return "Only metadata is changed.";
        default: return "Limited to the requested step.";
    }
}

function actionIcon(a: RecoveryAction) {
    switch (a) {
        case "approve": return <Checkmark16Regular />;
        case "decline": return <Dismiss16Regular />;
        case "request_alternative": return <ArrowClockwise16Regular />;
        case "edit_input": return <Edit16Regular />;
    }
}

export function ApprovalCard({
    step,
    title,
    summary,
    reversible,
    blastRadius,
    toolCallPreview,
    recoveryActions,
    busy,
    onAction,
}: ApprovalCardProps) {
    const { t } = useTranslation();
    const [previewOpen, setPreviewOpen] = useState(false);

    const isReversible = reversible ?? step.reversible;
    const blast = blastRadius ?? step.blastRadius ?? null;
    const preview = toolCallPreview ?? step.toolCallPreview ?? null;
    const actions = (recoveryActions && recoveryActions.length > 0)
        ? recoveryActions
        : (step.recoveryActions && step.recoveryActions.length > 0
            ? step.recoveryActions
            : DEFAULT_ACTIONS);

    const approveAction: RecoveryAction = "approve";
    const otherActions = actions.filter((a) => a !== approveAction);

    const requestTitle = title || t("Approval_Title");
    const requestSummary = summary || step.rationale || step.title;
    const primaryLabel = approvalVerb(step, requestSummary, preview);
    const reversibleCopy = isReversible ? "Yes — one click to un-certify." : t("Approval_Reversible_No");
    const toolName = preview?.name || step.toolCallPreview?.name || "tool call";

    return (
        <Card className="approval-card" role="region" aria-label={t("Approval_Title")}>
            <div className="approval-card__header">
                <ShieldCheckmark20Regular />
                <strong>{requestTitle}</strong>
                {step.target?.itemType && (
                    <span className="approval-card__agent-tag">{step.target.itemType}</span>
                )}
            </div>

            <div className="approval-card__request">
                <div className="approval-card__request-label">
                    <Warning20Regular />
                    <span>Agent request</span>
                </div>
                <p>"{requestSummary}"</p>
            </div>

            <div className="approval-card__meta">
                <div className="approval-card__row">
                    <span>What happens</span>
                    <p>{step.title || requestSummary}</p>
                </div>
                <div className="approval-card__row">
                    <span>Reversible?</span>
                    <p className={isReversible ? "approval-card__yes" : "approval-card__no"}>{reversibleCopy}</p>
                </div>
                <div className="approval-card__row">
                    <span>Blast radius</span>
                    <p>{blastCopy(blast)}</p>
                </div>
                <div className="approval-card__row">
                    <span>Tool call</span>
                    <code>{toolName}</code>
                </div>
            </div>

            {preview && (
                <div className="approval-card__preview">
                    <button
                        type="button"
                        className="approval-card__preview-toggle"
                        onClick={() => setPreviewOpen((v) => !v)}
                        aria-expanded={previewOpen}
                        aria-controls="approval-card-preview-body"
                    >
                        {previewOpen ? <ChevronDown16Regular /> : <ChevronRight16Regular />}
                        <span>reconciliation report</span>
                    </button>
                    {(previewOpen || true) && (
                        <pre
                            id="approval-card-preview-body"
                            className="approval-card__preview-body"
                        >{JSON.stringify(preview.args, null, 2)}</pre>
                    )}
                </div>
            )}

            <div className="approval-card__actions">
                {actions.includes(approveAction) && (
                    <button
                        type="button"
                        className="approval-card__action approval-card__action--primary"
                        disabled={busy}
                        onClick={() => onAction(approveAction)}
                    >
                        {actionIcon(approveAction)}
                        {primaryLabel}
                    </button>
                )}
                {otherActions.map((a) => (
                    <button
                        key={a}
                        type="button"
                        className={`approval-card__action approval-card__action--secondary${a === "decline" ? " approval-card__action--danger" : ""}`}
                        disabled={busy}
                        onClick={() => onAction(a)}
                    >
                        {actionIcon(a)}
                        {alternateLabel(a, requestSummary, preview)}
                    </button>
                ))}
            </div>
        </Card>
    );
}
