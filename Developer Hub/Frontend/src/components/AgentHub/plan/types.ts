/**
 * Plan TypeScript mirrors for the backend ``domain/models/plan`` Pydantic
 * models. Wire format is camelCase (backend controller dumps the plan with
 * ``by_alias=True``), so these interfaces use camelCase verbatim — no
 * transformation happens in the frontend.
 *
 * Keep this file in lock-step with ``Backend/src/domain/models/plan.py``.
 * When either side changes, update both.
 */

export type PlanStepAction =
    | "create"
    | "update"
    | "delete"
    | "configure"
    | "validate"
    | "clarify";

export type PlanRisk = "low" | "medium" | "high";

export type PlanPrereqStatus = "satisfied" | "missing" | "unknown";

export interface PlanTarget {
    itemType: string;
    displayName: string;
    workspaceId: string;
    existingItemId?: string | null;
}

export interface PlanStep {
    id: string;
    order: number;
    title: string;
    action: PlanStepAction;
    target: PlanTarget;
    inputs: string[];
    dependsOn: string[];
    rationale: string;
    risk: PlanRisk;
    riskNotes?: string | null;
    reversible: boolean;
    // NOTE: estimatedDurationSeconds was removed per spec §1. Do NOT
    // reintroduce it — Fabric operation duration depends too heavily on
    // capacity, data volume, and external latency.
}

// ── Spec §4: prerequisites carry category + applies_to_step_ids + verification ──

export type PlanPrereqCategory =
    | "workspace_role"
    | "tenant_scope"
    | "capacity"
    | "item_permission"
    | "source_access"
    | "connection"
    | "git_alm"
    | "feature_flag"
    | "license"
    | "quota";

export type PlanVerifierKind =
    | "fabric_api"
    | "graph_api"
    | "capacity_api"
    | "connection_probe"
    | "license_lookup"
    | "git_status"
    | "manual";

export interface PlanPrereqVerification {
    kind: PlanVerifierKind;
    spec: Record<string, unknown>;
    status: PlanPrereqStatus;
    checkedAt?: string | null;
    evidence?: string | null;
    unknownReason?: string | null;
}

export interface PlanPrerequisite {
    id: string;
    title: string;
    description: string;
    status: PlanPrereqStatus;
    evidence?: string | null;
    // Spec §4.2 additions
    text?: string | null;
    category: PlanPrereqCategory;
    appliesToStepIds: string[];
    verification: PlanPrereqVerification;
}

// ── Spec §2: workspaceItems replaces the legacy no-action section ──

export type PlanWorkspaceItemDisposition = "keep_as_is" | "will_be_changed";

export interface PlanWorkspaceItem {
    item: string;
    type: string;
    disposition: PlanWorkspaceItemDisposition;
    reason: string;
    drivenByStepId?: string | null;
}

/** Legacy shape kept only so old cached plans don't break type-narrowing. */
export interface PlanNoActionItem {
    itemType: string;
    displayName: string;
    reason: string;
}

export interface PlanConflict {
    itemType: string;
    displayName: string;
    description: string;
    resolutionOptions: string[];
}

export interface PlanClarification {
    question: string;
    blocksSteps: string[];
}

export interface PlanFooter {
    agentCount: number;
    stepCount: number;
    approvalPoints: number;
    executionBlocked: boolean;
}

export interface Plan {
    jobId: string;
    summary: string;
    assumptions: string[];
    prerequisites: PlanPrerequisite[];
    steps: PlanStep[];
    workspaceItems: PlanWorkspaceItem[];
    /** Legacy — new plans always use workspaceItems. Kept for decoder safety. */
    noAction: PlanNoActionItem[];
    conflicts: PlanConflict[];
    clarificationsNeeded: PlanClarification[];
    footer: PlanFooter;
}

/** True when the plan has at least one non-clarify step and no blockers. */
export function isPlanActionable(plan: Plan): boolean {
    if (plan.conflicts.length > 0) return false;
    if (plan.clarificationsNeeded.length > 0) return false;
    if (plan.footer?.executionBlocked) return false;
    return plan.steps.some((s) => s.action !== "clarify");
}
