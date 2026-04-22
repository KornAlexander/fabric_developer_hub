import React from "react";
import { useTranslation } from "react-i18next";
import {
    DocumentText20Filled,
    ChevronDown16Regular,
    Edit16Regular,
    Database16Regular,
    BuildingFactory16Regular,
    DocumentPdf16Regular,
    Document16Regular,
    Image16Regular,
    Folder16Regular,
    People16Regular,
    Shield16Regular,
    ShieldError16Regular,
    BranchFork16Regular,
    Play16Regular,
} from "@fluentui/react-icons";

/**
 * Recap of the user's task prompt shown at the top of Step 2. Uses
 * native ``<details>`` for expand/collapse so keyboard + screen-reader
 * semantics work out of the box.
 *
 * Layout rules (UX best practice — matches Linear / Notion / Raycast
 * recap cards):
 *   · Header row = eyebrow icon + label line (destination workspace +
 *     compact counts) + category badges + Edit + chevron.
 *   · When COLLAPSED the header also shows a one-line prompt preview.
 *   · When OPEN the body shows the full prompt exactly once, followed
 *     by grouped chip rows for: destination, added workspaces, items,
 *     and attachments. Chip colors match Step 1 (amber for files,
 *     violet for workspaces, teal for items) so the user sees the
 *     exact same pills they built in the composer.
 */

interface Attachment {
    name?: string;
    filename?: string;
    kind?: "text" | "image" | "pdf";
    mime?: string;
    mime_type?: string;
    size?: number;
    /** Classification assigned server-side by ``classify_attachments``.
     * Absent for older sessions — the UI renders no badge in that case.
     * ``severity="warn"`` ⇒ render a visible "flagged" badge. A
     * ``category="documentation"`` entry with ``markerCount>0`` gets a
     * subtle "Docs" badge so the user knows the file was recognised as
     * prose and not treated as an adversarial payload. */
    classification?: {
        severity: "info" | "warn";
        category: "clean" | "documentation" | "suspicious" | "skipped";
        markerCount: number;
        hasHighConfidence: boolean;
        documentLike: boolean;
        message: string;
    };
    // Pass-through fields so click handlers can re-use the full
    // attachment shape (content, previewUrl, etc.).
    [key: string]: any;
}

interface WorkspaceItem {
    name?: string;
    displayName?: string;
    type?: string;
    itemType?: string;
    id?: string;
    workspaceId?: string;
    [key: string]: any;
}

interface TaskPromptRecapProps {
    task: string;
    /** Destination workspace — where the run will execute. Shown with
     *  its own dedicated "Runs in" treatment, never counted as an
     *  added context item. */
    workspaceName?: string | null;
    /** Workspace id for the destination; enables click-to-preview. */
    workspaceId?: string | null;
    workspaceItems?: WorkspaceItem[] | null;
    attachments?: Attachment[] | null;
    requireApprovals?: boolean;
    branchOut?: boolean;
    /** Destination branch name — shown inline with the destination
     *  workspace on the same "Runs in" row, so branch info adds no
     *  extra vertical space. Only rendered when ``branchOut`` is on. */
    branchName?: string | null;
    /** Source workspace name — the workspace the branch was created
     *  from. Shown as a muted "from <source>" hint on the same
     *  "Runs in" row when ``branchOut`` is on and source differs
     *  from destination. Never takes its own row. */
    sourceWorkspaceName?: string | null;
    onEdit?: () => void;
    /** Uncontrolled initial state. Ignored when ``open`` is provided. */
    defaultOpen?: boolean;
    /** Controlled open state. When provided, ``onOpenChange`` should
     *  be wired so user clicks on the summary still toggle the recap. */
    open?: boolean;
    onOpenChange?: (open: boolean) => void;
    /** Click a context item (or added workspace) to preview it. */
    onItemClick?: (item: WorkspaceItem) => void;
    /** Click the destination workspace to preview it. */
    onWorkspaceClick?: (ws: { id: string; name: string }) => void;
    /** Click an attachment to preview it. */
    onAttachmentClick?: (att: Attachment) => void;
}

function classifyItem(type: string | undefined): "workspace" | "lakehouse" | "warehouse" | "item" {
    const t = (type || "").toLowerCase();
    if (t.includes("workspace")) return "workspace";
    if (t.includes("lakehouse")) return "lakehouse";
    if (t.includes("warehouse") || t.includes("sql")) return "warehouse";
    return "item";
}

function classifyFile(att: Attachment): "pdf" | "image" | "attachment" {
    if (att.kind === "pdf") return "pdf";
    if (att.kind === "image") return "image";
    const m = (att.mime || att.mime_type || "").toLowerCase();
    const n = (att.filename || att.name || "").toLowerCase();
    if (m.includes("pdf") || n.endsWith(".pdf")) return "pdf";
    if (m.startsWith("image/") || /\.(png|jpe?g|gif|webp|svg)$/.test(n)) return "image";
    return "attachment";
}

function fileIcon(kind: "pdf" | "image" | "attachment"): React.ReactElement {
    if (kind === "pdf") return <DocumentPdf16Regular />;
    if (kind === "image") return <Image16Regular />;
    return <Document16Regular />;
}

function itemIcon(kind: "workspace" | "lakehouse" | "warehouse" | "item"): React.ReactElement {
    if (kind === "workspace") return <Folder16Regular />;
    if (kind === "warehouse") return <BuildingFactory16Regular />;
    return <Database16Regular />;
}

export function TaskPromptRecap({
    task,
    workspaceName,
    workspaceId,
    workspaceItems,
    attachments,
    requireApprovals,
    branchOut,
    branchName,
    sourceWorkspaceName,
    onEdit,
    defaultOpen = true,
    open,
    onOpenChange,
    onItemClick,
    onWorkspaceClick,
    onAttachmentClick,
}: TaskPromptRecapProps) {
    const { t } = useTranslation();

    const allItems = (workspaceItems || []).filter(Boolean);
    const files = (attachments || []).filter(Boolean);

    // Split context items into added-workspaces vs fabric-items so counts
    // and chip groups don't conflate the two. The *destination* workspace
    // (`workspaceName`) lives in its own section — never counted here.
    const contextWorkspaces = allItems.filter((it) => classifyItem(it.itemType || it.type) === "workspace");
    const fabricItems = allItems.filter((it) => classifyItem(it.itemType || it.type) !== "workspace");

    const wsCount = contextWorkspaces.length;
    const itemCount = fabricItems.length;
    const fileCount = files.length;

    const countPart = (n: number, singular: string, plural: string) =>
        n === 0 ? null : `${n} ${n === 1 ? singular : plural}`;

    const countParts = [
        countPart(wsCount, "workspace", "workspaces"),
        countPart(itemCount, "item", "items"),
        countPart(fileCount, "file", "files"),
    ].filter(Boolean) as string[];

    /**
     * Build the props that make a chip behave like a button: role,
     * tabIndex, click, keyboard activation, and the `--clickable`
     * modifier (which adds the hover/focus affordances already defined
     * in styles.scss for Step 1 pills). Returns ``null``-ish props
     * when no handler is supplied so the chip stays presentational.
     */
    const clickableProps = (
        onClick: (() => void) | undefined,
        tip: string,
    ) => {
        if (!onClick) {
            return { title: tip, className: "" };
        }
        return {
            title: `${tip} · click to preview`,
            className: "ctx-pill--clickable",
            role: "button" as const,
            tabIndex: 0,
            onClick: (e: React.MouseEvent) => {
                e.stopPropagation();
                e.preventDefault();
                onClick();
            },
            onKeyDown: (e: React.KeyboardEvent) => {
                if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    e.stopPropagation();
                    onClick();
                }
            },
        };
    };

    const isControlled = open !== undefined;
    const effectiveOpen = isControlled ? open : defaultOpen;

    return (
        <details
            className="mc-prompt-recap"
            open={effectiveOpen}
            onToggle={(ev) => {
                if (onOpenChange) {
                    onOpenChange((ev.currentTarget as HTMLDetailsElement).open);
                }
            }}
        >
            <summary>
                <DocumentText20Filled className="mc-prompt-recap__eyebrow-icon" />

                <div className="mc-prompt-recap__main">
                    <div className="mc-prompt-recap__kicker">
                        <strong>{t("Recap_Task_Label") || "TASK PROMPT"}</strong>
                        {countParts.map((p, i) => (
                            <span key={i} className="mc-prompt-recap__count">· {p}</span>
                        ))}
                    </div>
                    {/* One-line preview shown only when collapsed — CSS
                       hides it when the <details> is open so the body
                       doesn't duplicate the prompt. */}
                    <div className="mc-prompt-recap__summary-text">
                        {task || ""}
                    </div>
                </div>

                {/* Inline category badges: only categories with a non-zero
                   count appear. Destination workspace is NOT counted
                   (it's not something the user "added"). */}
                <div className="mc-prompt-recap__inline-badges">
                    {wsCount > 0 && (
                        <span className="fabric-badge-workspace" title={`${wsCount} additional workspace${wsCount === 1 ? "" : "s"} in scope`}>
                            <Folder16Regular />{wsCount}
                        </span>
                    )}
                    {itemCount > 0 && (
                        <span className="fabric-badge-lakehouse" title={`${itemCount} item${itemCount === 1 ? "" : "s"} in scope`}>
                            <Database16Regular />{itemCount}
                        </span>
                    )}
                    {fileCount > 0 && (
                        <span className="fabric-badge-file" title={`${fileCount} attached file${fileCount === 1 ? "" : "s"}`}>
                            <DocumentPdf16Regular />{fileCount}
                        </span>
                    )}
                </div>

                <ChevronDown16Regular className="mc-prompt-recap__chevron" />
            </summary>

            <div className="mc-prompt-recap__body">
                {onEdit && (
                    <button
                        type="button"
                        className="mc-prompt-recap__edit"
                        onClick={(ev) => {
                            ev.stopPropagation();
                            onEdit();
                        }}
                        title={t("Recap_Edit") || "Edit task"}
                    >
                        <Edit16Regular />
                        {t("Recap_Edit") || "Edit"}
                    </button>
                )}
                {/* Prompt first — it's the primary content the user
                   authored. Metadata ("Runs in", chip groups, flags)
                   follows, flowing outward from content to mechanics. */}
                <div className="mc-prompt-recap__prompt-text">
                    {task}
                </div>

                {/* "Runs in" — the destination workspace (and, when
                   branching, the branch + source). Secondary to the
                   prompt itself. */}
                {workspaceName && (() => {
                    const canOpen = !!(onWorkspaceClick && workspaceId);
                    const cp = clickableProps(
                        canOpen ? () => onWorkspaceClick!({ id: workspaceId!, name: workspaceName }) : undefined,
                        `Runs in ${workspaceName}`,
                    );
                    return (
                        <div className="mc-prompt-recap__destination-row">
                            <span className="mc-prompt-recap__destination-label">
                                <Play16Regular />
                                Runs in
                            </span>
                            <div className="mc-prompt-recap__destination-chips">
                                <span
                                    {...cp}
                                    className={`ctx-pill ctx-pill--workspace ctx-pill--destination ${cp.className}`}
                                >
                                    <Folder16Regular />
                                    <span>{workspaceName}</span>
                                </span>
                                {branchOut && branchName && (
                                    <>
                                        <span className="mc-prompt-recap__destination-sep" aria-hidden="true">/</span>
                                        <span
                                            className="ctx-pill ctx-pill--branch"
                                            title={`Branch ${branchName}`}
                                        >
                                            <BranchFork16Regular />
                                            <span>{branchName}</span>
                                        </span>
                                        {sourceWorkspaceName && sourceWorkspaceName !== workspaceName && (
                                            <span
                                                className="mc-prompt-recap__destination-source"
                                                title={`Branched off ${sourceWorkspaceName}`}
                                            >
                                                from <em>{sourceWorkspaceName}</em>
                                            </span>
                                        )}
                                    </>
                                )}
                            </div>
                        </div>
                    );
                })()}

                {/* Chip groups — each category renders only if it has
                   content. Reuses the exact `ctx-pill--*` classes from
                   Step 1 so the visual language is identical. */}
                {(contextWorkspaces.length > 0 || fabricItems.length > 0 || files.length > 0) && (
                    <div className="mc-prompt-recap__chip-groups">
                        {contextWorkspaces.length > 0 && (
                            <div className="mc-prompt-recap__chip-group">
                                <span className="mc-prompt-recap__chip-group-label">
                                    <People16Regular />
                                    Workspace{contextWorkspaces.length === 1 ? "" : "s"}
                                </span>
                                <div className="mc-prompt-recap__chip-group-chips">
                                    {contextWorkspaces.map((it, i) => {
                                        const label = it.displayName || it.name || "Workspace";
                                        const cp = clickableProps(
                                            onItemClick ? () => onItemClick(it) : undefined,
                                            `${label} · Workspace`,
                                        );
                                        return (
                                            <span
                                                key={`cw-${i}`}
                                                {...cp}
                                                className={`ctx-pill ctx-pill--workspace ${cp.className}`}
                                            >
                                                <Folder16Regular />
                                                <span>{label}</span>
                                            </span>
                                        );
                                    })}
                                </div>
                            </div>
                        )}
                        {fabricItems.length > 0 && (
                            <div className="mc-prompt-recap__chip-group">
                                <span className="mc-prompt-recap__chip-group-label">
                                    <Database16Regular />
                                    Item{fabricItems.length === 1 ? "" : "s"}
                                </span>
                                <div className="mc-prompt-recap__chip-group-chips">
                                    {fabricItems.map((it, i) => {
                                        const kind = classifyItem(it.itemType || it.type);
                                        const variant = kind === "lakehouse" ? "lakehouse"
                                            : kind === "warehouse" ? "warehouse"
                                            : "item";
                                        const label = it.displayName || it.name || "Item";
                                        const cp = clickableProps(
                                            onItemClick ? () => onItemClick(it) : undefined,
                                            `${label} · ${it.itemType || it.type || "Item"}`,
                                        );
                                        return (
                                            <span
                                                key={`fi-${i}`}
                                                {...cp}
                                                className={`ctx-pill ctx-pill--${variant} ${cp.className}`}
                                            >
                                                {itemIcon(kind)}
                                                <span>{label}</span>
                                            </span>
                                        );
                                    })}
                                </div>
                            </div>
                        )}
                        {files.length > 0 && (
                            <div className="mc-prompt-recap__chip-group">
                                <span className="mc-prompt-recap__chip-group-label">
                                    <DocumentPdf16Regular />
                                    File{files.length === 1 ? "" : "s"}
                                </span>
                                <div className="mc-prompt-recap__chip-group-chips">
                                    {files.map((f, i) => {
                                        const kind = classifyFile(f);
                                        const label = f.filename || f.name || "attachment";
                                        const cp = clickableProps(
                                            onAttachmentClick ? () => onAttachmentClick(f) : undefined,
                                            label,
                                        );
                                        const cls = f.classification;
                                        // Only render a badge for genuinely suspicious files.
                                        // Documentation classification is recorded server-side
                                        // for audit/log triage but intentionally not shown to
                                        // the user — it's the absence of a flag that signals
                                        // "fine, treated as data".
                                        const showFlagged = cls?.severity === "warn";
                                        return (
                                            <span
                                                key={`f-${i}`}
                                                {...cp}
                                                className={`ctx-pill ctx-pill--${kind} ${cp.className}`}
                                            >
                                                {fileIcon(kind)}
                                                <span>{label}</span>
                                                {showFlagged && (
                                                    <span
                                                        className="ctx-pill__badge ctx-pill__badge--warn"
                                                        title={t("MissionControl_Attachment_Flagged_Tooltip")}
                                                        aria-label={t("MissionControl_Attachment_Flagged_Tooltip")}
                                                    >
                                                        <ShieldError16Regular />
                                                        {t("MissionControl_Attachment_Flagged_Badge")}
                                                    </span>
                                                )}
                                            </span>
                                        );
                                    })}
                                </div>
                            </div>
                        )}
                    </div>
                )}

                <div className="mc-prompt-recap__flags">
                    <span className="mc-prompt-recap__flag" data-state={requireApprovals ? "on" : "off"}>
                        <Shield16Regular />
                        Require approvals <strong>{requireApprovals ? "ON" : "OFF"}</strong>
                    </span>
                    <span className="mc-prompt-recap__flag" data-state={branchOut ? "on" : "off"}>
                        <BranchFork16Regular />
                        Branch out <strong>{branchOut ? "ON" : "OFF"}</strong>
                    </span>
                </div>
            </div>
        </details>
    );
}
