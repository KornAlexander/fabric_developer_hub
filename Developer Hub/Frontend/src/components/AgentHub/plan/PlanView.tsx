import React from "react";
import { Spinner } from "@fluentui/react-components";
import { useTranslation } from "react-i18next";
import type {
    Plan,
    PlanStep,
    PlanPrerequisite,
    PlanPrereqCategory,
    PlanWorkspaceItem,
    PlanConflict,
    PlanPrereqStatus,
} from "./types";
import { isPlanActionable } from "./types";

// Allowed Fabric item types (mirror of Backend ``VALID_ARTIFACT_TYPES``).
// Step targets outside this set render without a badge — defensive
// rendering per spec §3.4.
const VALID_ARTIFACT_TYPES = new Set([
    "Lakehouse", "Warehouse", "Pipeline", "Dataflow", "Notebook",
    "SemanticModel", "Report", "KQLDatabase", "Eventstream", "AISkill",
    "None", "workspace", "connection", "capacity",
]);

/** Scroll a step row into view and pulse it briefly — used when the
 * user clicks a step-id pill from a workspace_item / prereq. */
function pulseStep(stepId: string): void {
    const el = document.querySelector<HTMLElement>(
        `[data-testid="plan-step-${stepId}"]`,
    );
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    el.classList.add("plan-card__step--pulse");
    window.setTimeout(() => el.classList.remove("plan-card__step--pulse"), 800);
}

export interface PlanViewProps {
    plan: Plan;
    workspaceName: string;
    approving: boolean;
    /**
     * The ``require approvals`` toggle from the composer. When false no
     * step is gated for review regardless of its intrinsic risk — this
     * mirrors rule 7 in docs/plan-generation-overhaul.md.
     */
    requireApprovals?: boolean;
    onApprove: () => void;
    onReject: () => void;
}

/**
 * Plan Review — Material-3 card redesign per §3 of
 * docs/plan-generation-overhaul.md. Every section the spec names is
 * preserved (Summary, Assumptions, Prerequisites, Steps,
 * Already-satisfied, Risks) but laid out inside one card matching
 * Design/new_session_step2_plan_review.html. Wire shape stays the
 * existing camelCase Plan from plan/types.ts.
 */
export function PlanView({
    plan,
    workspaceName,
    approving,
    requireApprovals = false,
    onApprove,
    onReject,
}: PlanViewProps): JSX.Element {
    const { t } = useTranslation();
    const actionable = isPlanActionable(plan);

    const stepNeedsApproval = (s: PlanStep): boolean => {
        if (!requireApprovals) return false;
        return !s.reversible || s.risk === "high" || s.action === "delete";
    };

    const steps = [...plan.steps].sort((a, b) => a.order - b.order);
    // Spec §4.4: backend stamps ``footer.executionBlocked`` when any
    // prerequisite is missing. UI uses it to disable Execute.
    const executionBlocked = !!plan.footer?.executionBlocked;
    // Counts prefer the backend-computed footer values but fall back to
    // client-side calcs so tests and legacy payloads keep working.
    const approvalCount =
        plan.footer?.approvalPoints ?? steps.filter(stepNeedsApproval).length;
    const stepCount = plan.footer?.stepCount ?? steps.length;
    const agentCount =
        plan.footer?.agentCount ?? steps.filter((s) => s.action !== "clarify").length;

    const anyUnknown = plan.prerequisites.some(
        (p) => (p.verification?.status ?? p.status) === "unknown",
    );
    // Spec §2: workspace_items replaces the legacy no-action list. A
    // server that still emits ``noAction`` has already been migrated to
    // ``workspaceItems`` by the backend ``_parse_plan``, but we fall
    // back defensively so cached plans keep rendering.
    const workspaceItems: PlanWorkspaceItem[] =
        plan.workspaceItems && plan.workspaceItems.length > 0
            ? plan.workspaceItems
            : (plan.noAction || []).map<PlanWorkspaceItem>((n) => ({
                  item: n.displayName,
                  type: n.itemType,
                  disposition: "keep_as_is",
                  reason: n.reason,
                  drivenByStepId: null,
              }));

    return (
        <section
            className="plan-card"
            data-testid="plan"
            aria-label={t("Plan_Section_Label")}
        >
            <header className="plan-card__hero">
                <span className="plan-card__hero-icon" aria-hidden>
                    <MatIcon name="auto_awesome" />
                </span>
                <div>
                    <h3 className="plan-card__hero-title">{t("Plan_Title")}</h3>
                    <p className="plan-card__hero-sub">
                        {t("Plan_Workspace_Label")}:{" "}
                        <strong>{workspaceName}</strong>
                    </p>
                </div>
            </header>

            <div className="plan-card__body">
                <PlanSection icon="summarize" label={t("Plan_Summary_Heading")}>
                    <p className="plan-card__summary-text">{plan.summary}</p>
                </PlanSection>

                {plan.assumptions.length > 0 && (
                    <PlanSection
                        icon="lightbulb"
                        label={t("Plan_Assumptions_Title")}
                    >
                        <ul
                            className="plan-card__pill-row"
                            data-testid="plan-assumptions"
                        >
                            {plan.assumptions.map((a, i) => (
                                <li
                                    key={i}
                                    className="plan-card__pill plan-card__pill--neutral"
                                >
                                    <span className="plan-card__pill-dot plan-card__pill-dot--amber" />
                                    <span>{a}</span>
                                </li>
                            ))}
                        </ul>
                    </PlanSection>
                )}

                {plan.prerequisites.length > 0 && (
                    <PlanSection
                        icon="checklist"
                        label={t("Plan_Prereqs_Title")}
                    >
                        <ul
                            className="plan-card__prereqs"
                            data-testid="plan-prereqs"
                        >
                            {plan.prerequisites.map((p) => (
                                <PrereqRow key={p.id} p={p} />
                            ))}
                        </ul>
                        {anyUnknown && (
                            <div
                                className="plan-card__banner plan-card__banner--warning plan-card__banner--compact"
                                role="note"
                                data-testid="plan-prereqs-unknown-notice"
                            >
                                <MatIcon name="help" />
                                <span>{t("Plan_Prereqs_Unknown_Notice")}</span>
                            </div>
                        )}
                    </PlanSection>
                )}

                {plan.clarificationsNeeded.length > 0 && (
                    <BlockingBanner
                        tone="warning"
                        title={t("Plan_Clarifications_Title")}
                        items={plan.clarificationsNeeded.map((c) => c.question)}
                        testId="plan-clarifications"
                    />
                )}

                <PlanSection
                    icon="account_tree"
                    label={t("Plan_Steps_Title")}
                    hideWhenEmpty={false}
                >
                    {steps.length > 0 ? (
                        <ol
                            className="plan-card__steps"
                            data-testid="plan-steps"
                        >
                            {steps.map((s, idx) => (
                                <StepRow
                                    key={s.id}
                                    step={s}
                                    index={idx + 1}
                                    needsApproval={stepNeedsApproval(s)}
                                />
                            ))}
                        </ol>
                    ) : (
                        <EmptyStepsRow />
                    )}
                </PlanSection>

                {plan.conflicts.length > 0 && (
                    <RisksSection conflicts={plan.conflicts} />
                )}

                {workspaceItems.length > 0 && (
                    <WorkspaceItemsSection items={workspaceItems} />
                )}
            </div>

            <footer className="plan-card__footer">
                <div className="plan-card__footer-meta">
                    <Stat
                        icon="smart_toy"
                        labelKey="Plan_Meta_AgentsLabel"
                        value={agentCount}
                    />
                    <Stat
                        icon="format_list_numbered"
                        labelKey="Plan_Meta_StepsLabel"
                        value={stepCount}
                    />
                    {approvalCount > 0 && (
                        <Stat
                            icon="shield_person"
                            labelKey="Plan_Meta_ApprovalsLabel"
                            value={approvalCount}
                            accent="amber"
                        />
                    )}
                </div>
                <div className="plan-card__footer-actions">
                    <button
                        type="button"
                        className="plan-card__btn plan-card__btn--tonal"
                        onClick={onReject}
                        disabled={approving}
                        data-testid="plan-reject-btn"
                    >
                        <MatIcon name="edit" />
                        <span>{t("Plan_Action_Reject")}</span>
                    </button>
                    <button
                        type="button"
                        className="plan-card__btn plan-card__btn--primary"
                        onClick={onApprove}
                        disabled={approving || !actionable || executionBlocked}
                        data-testid="plan-approve-btn"
                        title={
                            executionBlocked
                                ? t("Plan_Action_BlockedByPrereqs")
                                : !actionable
                                ? t("Plan_Action_BlockedNote")
                                : undefined
                        }
                    >
                        {approving ? (
                            <Spinner size="tiny" />
                        ) : (
                            <MatIcon name="play_arrow" />
                        )}
                        <span>
                            {approving
                                ? t("Plan_Action_Approving")
                                : t("Plan_Action_Approve")}
                        </span>
                    </button>
                </div>
            </footer>
        </section>
    );
}

/* ═════════════════════════════════════════════════════════════════ */
/* Sub-components                                                     */
/* ═════════════════════════════════════════════════════════════════ */

function PlanSection({
    icon,
    label,
    children,
    hideWhenEmpty = true,
}: {
    icon: string;
    label: string;
    children: React.ReactNode;
    hideWhenEmpty?: boolean;
}): JSX.Element | null {
    if (hideWhenEmpty && React.Children.count(children) === 0) return null;
    return (
        <section className="plan-card__section">
            <h4 className="plan-card__section-head">
                <MatIcon name={icon} className="plan-card__section-icon" />
                <span>{label}</span>
            </h4>
            {children}
        </section>
    );
}

function StepRow({
    step,
    index,
    needsApproval,
}: {
    step: PlanStep;
    index: number;
    needsApproval: boolean;
}): JSX.Element {
    const { t } = useTranslation();
    const artifact = step.target.itemType || "None";
    const badgeClass = artifactBadgeClass(artifact);
    const badgeIcon = artifactBadgeIcon(artifact);

    return (
        <li
            className={`plan-card__step ${
                needsApproval ? "plan-card__step--approval" : ""
            }`}
            data-testid={`plan-step-${step.id}`}
        >
            <span
                className={`plan-card__step-index ${
                    needsApproval ? "plan-card__step-index--approval" : ""
                }`}
                aria-hidden
            >
                {needsApproval ? (
                    <MatIcon name="shield_person" />
                ) : (
                    <span className="plan-card__step-index-num">{index}</span>
                )}
            </span>
            <div className="plan-card__step-body">
                <div className="plan-card__step-title-row">
                    <span className="plan-card__step-title">{step.title}</span>
                    {artifact && artifact !== "None" && VALID_ARTIFACT_TYPES.has(artifact) && (
                        <span className={`fabric-badge ${badgeClass}`}>
                            <MatIcon name={badgeIcon} />
                            {artifact}
                        </span>
                    )}
                    {needsApproval && (
                        <span className="fabric-badge fabric-badge--approval">
                            <MatIcon name="shield_person" />
                            {t("Plan_Step_ApprovalNeeded")}
                        </span>
                    )}
                    {!step.reversible && !needsApproval && (
                        <span
                            className="fabric-badge fabric-badge--irreversible"
                            title={t("Plan_Step_Irreversible")}
                        >
                            <MatIcon name="warning" />
                            {t("Plan_Step_Irreversible")}
                        </span>
                    )}
                </div>
                <p className="plan-card__step-desc">{step.rationale}</p>
                {step.riskNotes && (
                    <p className="plan-card__step-risknote">
                        <MatIcon name="warning" />
                        {step.riskNotes}
                    </p>
                )}
            </div>
            <div className="plan-card__step-meta">
                <span
                    className={`plan-card__step-mode ${
                        needsApproval ? "plan-card__step-mode--review" : ""
                    }`}
                >
                    {needsApproval
                        ? t("Plan_Step_Mode_Review")
                        : t("Plan_Step_Mode_Auto")}
                </span>
                {/* Time estimates removed per spec §1. */}
            </div>
        </li>
    );
}

function PrereqRow({ p }: { p: PlanPrerequisite }): JSX.Element {
    const { t } = useTranslation();
    const status: PlanPrereqStatus =
        p.verification?.status ?? p.status ?? "unknown";
    const evidence =
        p.verification?.evidence ??
        p.verification?.unknownReason ??
        p.evidence ??
        "";
    const category: PlanPrereqCategory = p.category ?? "workspace_role";
    const label = p.text || p.title;
    const stepIds = p.appliesToStepIds ?? [];

    const icon =
        status === "satisfied"
            ? "check_circle"
            : status === "missing"
            ? "cancel"
            : "help";

    return (
        <li
            className={`plan-card__prereq plan-card__prereq--${status}`}
            data-testid={`plan-prereq-${p.id}`}
        >
            <span
                className={`plan-card__prereq-dot plan-card__prereq-dot--${status}`}
                aria-hidden
            />
            <MatIcon
                name={icon}
                className={`plan-card__prereq-icon plan-card__prereq-icon--${status}`}
            />
            <div className="plan-card__prereq-body">
                <div className="plan-card__prereq-title">
                    <strong>{label}</strong>
                    <span
                        className={`plan-card__prereq-cat plan-card__prereq-cat--${category}`}
                    >
                        {t(`Plan_Prereqs_Category_${category}` as const, {
                            defaultValue: category.replace(/_/g, " "),
                        })}
                    </span>
                </div>
                {p.description && p.description !== label && (
                    <p className="plan-card__prereq-desc">{p.description}</p>
                )}
                {evidence && (
                    <p className="plan-card__prereq-evidence">{evidence}</p>
                )}
                {stepIds.length > 0 && (
                    <div className="plan-card__prereq-steps">
                        <span className="plan-card__prereq-steps-label">
                            {t("Plan_Prereqs_AppliesTo")}
                        </span>
                        {stepIds.map((sid) => (
                            <button
                                type="button"
                                key={sid}
                                className="plan-card__step-pill"
                                onClick={() => pulseStep(sid)}
                                title={t("Plan_Prereqs_AppliesTo")}
                            >
                                {sid}
                            </button>
                        ))}
                    </div>
                )}
            </div>
            {(status === "missing" || status === "unknown") && (
                <button
                    type="button"
                    className="plan-card__btn plan-card__btn--tonal plan-card__prereq-recheck"
                    data-testid={`plan-prereq-recheck-${p.id}`}
                    onClick={() => {
                        // Recheck is spec §4.4 — the real server-side
                        // re-verification hook is wired by the session
                        // store; for now surfacing evidence is enough to
                        // give the user something actionable.
                        if (evidence) window.alert(evidence);
                    }}
                >
                    <MatIcon name="refresh" />
                    <span>{t("Plan_Prereqs_Recheck")}</span>
                </button>
            )}
        </li>
    );
}

function WorkspaceItemsSection({
    items,
}: {
    items: PlanWorkspaceItem[];
}): JSX.Element {
    const { t } = useTranslation();
    const [open, setOpen] = React.useState(false);

    const keep = items.filter((i) => i.disposition === "keep_as_is");
    const change = items.filter((i) => i.disposition === "will_be_changed");

    return (
        <section className="plan-card__section plan-card__section--workspace-items">
            <button
                type="button"
                className="plan-card__section-head plan-card__section-head--toggle"
                onClick={() => setOpen((v) => !v)}
                aria-expanded={open}
            >
                <MatIcon
                    name="inventory_2"
                    className="plan-card__section-icon"
                />
                <span>{t("Plan_WorkspaceItems_Title")}</span>
                <span className="plan-card__section-count">{items.length}</span>
                <MatIcon
                    name={open ? "expand_less" : "expand_more"}
                    className="plan-card__section-chevron"
                />
            </button>
            {open && (
                <div data-testid="plan-workspace-items">
                    {keep.length > 0 && (
                        <WorkspaceItemsGroup
                            label={t("Plan_WorkspaceItems_KeepAsIs")}
                            icon="check_circle"
                            tone="emerald"
                            items={keep}
                        />
                    )}
                    {change.length > 0 && (
                        <WorkspaceItemsGroup
                            label={t("Plan_WorkspaceItems_WillChange")}
                            icon="edit"
                            tone="amber"
                            items={change}
                        />
                    )}
                </div>
            )}
        </section>
    );
}

function WorkspaceItemsGroup({
    label,
    icon,
    tone,
    items,
}: {
    label: string;
    icon: string;
    tone: "emerald" | "amber";
    items: PlanWorkspaceItem[];
}): JSX.Element {
    const { t } = useTranslation();
    return (
        <div className={`plan-card__wi-group plan-card__wi-group--${tone}`}>
            <h5 className="plan-card__wi-group-head">
                <MatIcon
                    name={icon}
                    className={`plan-card__wi-group-icon plan-card__wi-group-icon--${tone}`}
                />
                <span>{label}</span>
                <span className="plan-card__section-count">{items.length}</span>
            </h5>
            <ul className="plan-card__satisfied-list">
                {items.map((w, i) => (
                    <li key={`${w.type}-${w.item}-${i}`}>
                        <span
                            className={`fabric-badge ${artifactBadgeClass(
                                w.type,
                            )}`}
                        >
                            <MatIcon name={artifactBadgeIcon(w.type)} />
                            {w.type}
                        </span>
                        <strong>{w.item}</strong>
                        <span className="plan-card__satisfied-reason">
                            {w.reason}
                        </span>
                        {w.drivenByStepId && (
                            <button
                                type="button"
                                className="plan-card__step-pill"
                                onClick={() =>
                                    w.drivenByStepId && pulseStep(w.drivenByStepId)
                                }
                                title={t("Plan_WorkspaceItems_DrivenBy", {
                                    step: w.drivenByStepId,
                                })}
                            >
                                {t("Plan_WorkspaceItems_DrivenBy", {
                                    step: w.drivenByStepId,
                                })}
                            </button>
                        )}
                    </li>
                ))}
            </ul>
        </div>
    );
}

function RisksSection({
    conflicts,
}: {
    conflicts: PlanConflict[];
}): JSX.Element {
    const { t } = useTranslation();
    return (
        <section className="plan-card__section plan-card__section--risks">
            <h4 className="plan-card__section-head">
                <MatIcon
                    name="warning"
                    className="plan-card__section-icon plan-card__section-icon--amber"
                />
                <span>{t("Plan_Risks_Title")}</span>
            </h4>
            <ul className="plan-card__risks" data-testid="plan-conflicts">
                {conflicts.map((c, i) => (
                    <li key={`${c.itemType}-${c.displayName}-${i}`}>
                        <span className="plan-card__risk-dot plan-card__risk-dot--high" />
                        <div>
                            <div>
                                <strong>{c.displayName}</strong>
                                <span className="plan-card__risk-type">
                                    ({c.itemType})
                                </span>
                                {" — "}
                                {c.description}
                            </div>
                            {c.resolutionOptions.length > 0 && (
                                <div className="plan-card__risk-mitigation">
                                    {c.resolutionOptions.join(" · ")}
                                </div>
                            )}
                        </div>
                    </li>
                ))}
            </ul>
        </section>
    );
}

function BlockingBanner({
    tone,
    title,
    items,
    testId,
}: {
    tone: "error" | "warning";
    title: string;
    items: string[];
    testId?: string;
}): JSX.Element {
    return (
        <div
            className={`plan-card__banner plan-card__banner--${tone}`}
            role="alert"
            data-testid={testId}
        >
            <MatIcon name={tone === "error" ? "error" : "help"} />
            <div>
                <strong>{title}</strong>
                <ul>
                    {items.map((it, i) => (
                        <li key={i}>{it}</li>
                    ))}
                </ul>
            </div>
        </div>
    );
}

function EmptyStepsRow(): JSX.Element {
    const { t } = useTranslation();
    return (
        <div className="plan-card__empty">
            <MatIcon name="error" />
            <div>
                <strong>{t("Plan_NoSteps_Title")}</strong>
                <p>{t("Plan_NoSteps_Message")}</p>
            </div>
        </div>
    );
}

function Stat({
    icon,
    labelKey,
    textLabel,
    value,
    accent,
}: {
    icon: string;
    labelKey?: string;
    textLabel?: string;
    value?: number;
    accent?: "amber";
}): JSX.Element {
    const { t } = useTranslation();
    const label = textLabel ?? (labelKey ? t(labelKey) : "");
    return (
        <span
            className={`plan-card__stat ${
                accent === "amber" ? "plan-card__stat--amber" : ""
            }`}
        >
            <MatIcon name={icon} />
            {typeof value === "number" ? (
                <>
                    {label}: <strong>{value}</strong>
                </>
            ) : (
                <span>{label}</span>
            )}
        </span>
    );
}

function MatIcon({
    name,
    className,
}: {
    name: string;
    className?: string;
}): JSX.Element {
    return (
        <span
            className={`material-symbols-outlined ${className ?? ""}`.trim()}
            aria-hidden
        >
            {name}
        </span>
    );
}

/* ── artifact → badge mapping ─────────────────────────────────────── */

function artifactBadgeClass(itemType: string): string {
    const k = (itemType || "").toLowerCase();
    switch (k) {
        case "lakehouse":
            return "fabric-badge-lakehouse";
        case "warehouse":
            return "fabric-badge-warehouse";
        case "pipeline":
        case "datapipeline":
            return "fabric-badge-pipeline";
        case "notebook":
            return "fabric-badge-notebook";
        case "semanticmodel":
        case "semantic_model":
            return "fabric-badge-semanticmodel";
        case "report":
            return "fabric-badge-report";
        case "dataflow":
        case "dataflowgen2":
            return "fabric-badge-dataflow";
        case "kqldatabase":
        case "kql_database":
            return "fabric-badge-kqldatabase";
        case "eventstream":
            return "fabric-badge-eventstream";
        case "aiskill":
        case "ai_skill":
            return "fabric-badge-aiskill";
        default:
            return "fabric-badge-generic";
    }
}

function artifactBadgeIcon(itemType: string): string {
    const k = (itemType || "").toLowerCase();
    switch (k) {
        case "lakehouse":
            return "database";
        case "warehouse":
            return "warehouse";
        case "pipeline":
        case "datapipeline":
            return "conversion_path";
        case "notebook":
            return "book";
        case "semanticmodel":
        case "semantic_model":
            return "model_training";
        case "report":
            return "insert_chart";
        case "dataflow":
        case "dataflowgen2":
            return "bolt";
        case "kqldatabase":
        case "kql_database":
            return "database";
        case "eventstream":
            return "flash_on";
        case "aiskill":
        case "ai_skill":
            return "auto_awesome";
        default:
            return "draft";
    }
}

export default PlanView;
