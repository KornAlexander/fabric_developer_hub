import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import {
    Button,
    Card,
    Body1,
    Caption1,
    Subtitle2,
    Badge,
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

const BLAST_LABEL_KEY: Record<BlastRadius, string> = {
    "workspace": "Approval_BlastRadius_Workspace",
    "item": "Approval_BlastRadius_Item",
    "row-level": "Approval_BlastRadius_RowLevel",
    "metadata-only": "Approval_BlastRadius_MetadataOnly",
};

const BLAST_COLOR: Record<BlastRadius, "danger" | "warning" | "informative" | "subtle"> = {
    "workspace": "danger",
    "item": "warning",
    "row-level": "informative",
    "metadata-only": "subtle",
};

const DEFAULT_ACTIONS: RecoveryAction[] = [
    "approve",
    "decline",
    "request_alternative",
    "edit_input",
];

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

    const actionLabel: Record<RecoveryAction, string> = {
        approve: t("Approval_Action_Approve"),
        decline: t("Approval_Action_Decline"),
        request_alternative: t("Approval_Action_Alternative"),
        edit_input: t("Approval_Action_Edit"),
    };

    return (
        <Card className="approval-card" role="region" aria-label={t("Approval_Title")}>
            <div className="approval-card__header">
                <ShieldCheckmark20Regular />
                <Subtitle2>{title || t("Approval_Title")}</Subtitle2>
            </div>

            <Body1 className="approval-card__summary">
                {summary || step.rationale || step.title}
            </Body1>

            <div className="approval-card__meta">
                <Badge
                    appearance="outline"
                    color={isReversible ? "informative" : "danger"}
                    icon={isReversible ? <Checkmark16Regular /> : <Warning20Regular />}
                >
                    {isReversible ? t("Approval_Reversible_Yes") : t("Approval_Reversible_No")}
                </Badge>
                {blast && (
                    <Badge
                        appearance="filled"
                        color={BLAST_COLOR[blast]}
                        className={`fabric-badge fabric-badge--blast-${blast}`}
                    >
                        {t(BLAST_LABEL_KEY[blast])}
                    </Badge>
                )}
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
                        <Caption1>
                            <strong>Tool call:</strong> <code>{preview.name}</code>
                        </Caption1>
                    </button>
                    {previewOpen && (
                        <pre
                            id="approval-card-preview-body"
                            className="approval-card__preview-body"
                        >{JSON.stringify(preview.args, null, 2)}</pre>
                    )}
                </div>
            )}

            <div className="approval-card__actions">
                {actions.includes(approveAction) && (
                    <Button
                        appearance="primary"
                        icon={actionIcon(approveAction)}
                        disabled={busy}
                        onClick={() => onAction(approveAction)}
                    >
                        {actionLabel[approveAction]}
                    </Button>
                )}
                {otherActions.map((a) => (
                    <Button
                        key={a}
                        appearance="secondary"
                        icon={actionIcon(a)}
                        disabled={busy}
                        onClick={() => onAction(a)}
                    >
                        {actionLabel[a]}
                    </Button>
                ))}
            </div>
        </Card>
    );
}
