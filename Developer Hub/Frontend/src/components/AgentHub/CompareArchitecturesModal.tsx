import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Dismiss20Regular, ErrorCircle20Regular, ArrowLeft20Regular } from "@fluentui/react-icons";
import { listArchitectures, ArchitectureEntry } from "../../controller/AgentHubApi";
import { OrchCanvas, naturalCanvasSize } from "./team/OrchCanvas";
import type { Team } from "./plan/types";

/**
 * "Compare architectures" modal — shows every architecture the composer
 * can pick from, with a small orchestration graph for each plus the
 * copy the compose LLM sees (headline / description / pick-when /
 * watch-for / Fabric use cases).
 *
 * Architecture metadata comes from ``/api/catalogs/architectures`` so
 * this view stays in lock-step with the backend catalog.
 *
 * The orchestration graph per entry is a *synthetic* illustrative team
 * built client-side (the backend catalog doesn't carry example teams).
 * Using ``OrchCanvas`` — the same renderer Step 2 uses for real
 * compositions — keeps the visual language identical.
 */

type SampleTeamBuilder = () => Team;

/** Synthetic teams illustrating each architecture.
 *
 * Shapes intentionally mirror the real layouts ``OrchCanvas`` produces
 * (supervisor = hub-and-spoke, sequential = linear pipeline, network =
 * peer mesh, hierarchical = tree, solo = single node, mixed = grouped).
 */
const SAMPLE_TEAMS: Record<string, SampleTeamBuilder> = {
    solo: () => ({
        pattern: "solo",
        nodes: [
            { id: "a1", agent: "FabricDataEngineer", role: "Specialist", status: "planned" },
        ],
        edges: [],
    }),
    supervisor: () => ({
        pattern: "supervisor",
        nodes: [
            { id: "lead", agent: "Architect", role: "Planning lead", status: "planned" },
            { id: "a1", agent: "FabricDataEngineer", role: "Data engineer", status: "planned" },
            { id: "a2", agent: "FabricAdmin", role: "Governance", status: "planned" },
            { id: "a3", agent: "Modeler", role: "Reporting", status: "planned" },
        ],
        edges: [
            { from: "lead", to: "a1", kind: "delegate" },
            { from: "lead", to: "a2", kind: "delegate" },
            { from: "lead", to: "a3", kind: "delegate" },
        ],
    }),
    sequential: () => ({
        pattern: "sequential",
        nodes: [
            { id: "a1", agent: "FabricDataEngineer", role: "Bronze ingest", status: "planned" },
            { id: "a2", agent: "FabricDataEngineer", role: "Silver transform", status: "planned" },
            { id: "a3", agent: "Modeler", role: "Gold model", status: "planned" },
            { id: "a4", agent: "Creator", role: "Publish", status: "planned" },
        ],
        edges: [
            { from: "a1", to: "a2", kind: "report" },
            { from: "a2", to: "a3", kind: "report" },
            { from: "a3", to: "a4", kind: "report" },
        ],
    }),
    hierarchical: () => ({
        pattern: "hierarchical",
        nodes: [
            { id: "lead", agent: "Architect", role: "Program lead", status: "planned" },
            { id: "sl1", agent: "FabricDataEngineer", role: "Data sub-lead", status: "planned" },
            { id: "sl2", agent: "FabricAdmin", role: "Governance sub-lead", status: "planned" },
            { id: "sl3", agent: "Modeler", role: "Reporting sub-lead", status: "planned" },
        ],
        edges: [
            { from: "lead", to: "sl1", kind: "delegate" },
            { from: "lead", to: "sl2", kind: "delegate" },
            { from: "lead", to: "sl3", kind: "delegate" },
            { from: "sl1", to: "lead", kind: "report" },
            { from: "sl2", to: "lead", kind: "report" },
            { from: "sl3", to: "lead", kind: "report" },
        ],
    }),
    reflection: () => ({
        pattern: "network",
        nodes: [
            { id: "actor", agent: "FabricDataEngineer", role: "Actor — drafts", status: "planned" },
            { id: "critic", agent: "FabricDataEngineer", role: "Critic — reviews", status: "planned" },
        ],
        edges: [
            { from: "actor", to: "critic", kind: "peer" },
            { from: "critic", to: "actor", kind: "peer" },
        ],
    }),
    mixed: () => ({
        pattern: "mixed",
        nodes: [
            { id: "lead", agent: "Architect", role: "Planning lead", status: "planned" },
            { id: "a1", agent: "FabricAdmin", role: "Diagnostics sub-team", status: "planned" },
            { id: "a2", agent: "FabricDataEngineer", role: "Remediation pipeline", status: "planned" },
            { id: "a3", agent: "Modeler", role: "Reporter", status: "planned" },
        ],
        edges: [
            { from: "lead", to: "a1", kind: "delegate" },
            { from: "lead", to: "a2", kind: "delegate" },
            { from: "a2", to: "a3", kind: "delegate" },
        ],
    }),
    network: () => ({
        pattern: "network",
        nodes: [
            { id: "a1", agent: "FabricDataEngineer", role: "Peer", status: "planned" },
            { id: "a2", agent: "Modeler", role: "Peer", status: "planned" },
            { id: "a3", agent: "FabricAdmin", role: "Peer", status: "planned" },
            { id: "a4", agent: "Architect", role: "Peer", status: "planned" },
        ],
        edges: [
            { from: "a1", to: "a2", kind: "peer" },
            { from: "a2", to: "a3", kind: "peer" },
            { from: "a3", to: "a4", kind: "peer" },
            { from: "a4", to: "a1", kind: "peer" },
            { from: "a1", to: "a3", kind: "peer" },
            { from: "a2", to: "a4", kind: "peer" },
        ],
    }),
};

function sampleTeamFor(id: string): Team {
    const builder = SAMPLE_TEAMS[id] ?? SAMPLE_TEAMS.supervisor;
    return builder();
}

export interface CompareArchitecturesModalProps {
    open: boolean;
    onClose: () => void;
    /** Optional callback: when the user picks an architecture the caller
     *  can trigger a recompose with that preference. Omitted in preview
     *  contexts where the modal is purely informational. */
    onPick?: (architectureId: string) => void;
    /** Architecture id currently active on Step 2 — highlighted in the grid. */
    currentArchitecture?: string | null;
    /** Injected so the modal works without a live backend (tests,
     *  Storybook). When omitted, hits ``/api/catalogs/architectures``. */
    fetchArchitectures?: () => Promise<ArchitectureEntry[]>;
}

export function CompareArchitecturesModal({
    open,
    onClose,
    onPick,
    currentArchitecture,
    fetchArchitectures,
}: CompareArchitecturesModalProps) {
    const { t } = useTranslation();
    const [entries, setEntries] = useState<ArchitectureEntry[] | null>(null);
    const [error, setError] = useState<string | null>(null);
    /** When set, shows a full-view detail panel for this architecture. */
    const [expandedId, setExpandedId] = useState<string | null>(null);

    const onExpand = useCallback((id: string) => setExpandedId(id), []);
    const onCollapse = useCallback(() => setExpandedId(null), []);

    useEffect(() => {
        if (!open) return undefined;
        if (entries) return undefined;
        let cancelled = false;
        (async () => {
            try {
                const rows = fetchArchitectures
                    ? await fetchArchitectures()
                    : await listArchitectures({});
                if (!cancelled) setEntries(rows);
            } catch (e: any) {
                if (!cancelled) setError(String(e?.message || e));
            }
        })();
        return () => { cancelled = true; };
    }, [open, entries, fetchArchitectures]);

    // Close on Escape.
    useEffect(() => {
        if (!open) return undefined;
        const onKey = (e: KeyboardEvent) => {
            if (e.key === "Escape") {
                if (expandedId) setExpandedId(null);
                else onClose();
            }
        };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [open, onClose, expandedId]);

    // Lock body scroll while the modal is open.
    useEffect(() => {
        if (!open) return undefined;
        const prev = document.body.style.overflow;
        document.body.style.overflow = "hidden";
        return () => { document.body.style.overflow = prev; };
    }, [open]);

    const sorted = useMemo(() => {
        if (!entries) return null;
        // Drivered architectures first (the ones the composer actually
        // uses); reserved values are still worth showing so users see
        // the full design space, but visually de-emphasised.
        return [...entries].sort((a, b) => {
            if (a.hasDriver !== b.hasDriver) return a.hasDriver ? -1 : 1;
            return a.name.localeCompare(b.name);
        });
    }, [entries]);

    if (!open) return null;

    return (
        <div
            className="mc-compare-overlay"
            role="dialog"
            aria-modal="true"
            aria-labelledby="mc-compare-title"
            onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
        >
            <div className="mc-compare-dialog">
                <header className="mc-compare-header">
                    <div>
                        <h2 id="mc-compare-title" className="mc-compare-title">
                            {t("Compare_Title", "Compare architectures")}
                        </h2>
                        <p className="mc-compare-subtitle">
                            {t("Compare_Subtitle", "The composer picks the architecture that best fits your task. Here\u2019s the full catalogue it chooses from.")}
                        </p>
                    </div>
                    <button
                        type="button"
                        className="mc-compare-close"
                        onClick={onClose}
                        aria-label={t("Compare_Close", "Close")}
                    >
                        <Dismiss20Regular />
                    </button>
                </header>

                {error && (
                    <div className="mc-compare-error">
                        <ErrorCircle20Regular />
                        <span>{error}</span>
                    </div>
                )}

                {!sorted && !error && (
                    <div className="mc-compare-loading">
                        {t("Compare_Loading", "Loading architectures…")}
                    </div>
                )}

                {sorted && !expandedId && (
                    <div className="mc-compare-grid">
                        {sorted.map((arch) => {
                            const team = sampleTeamFor(arch.id);
                            const isCurrent = currentArchitecture === arch.id;
                            const nat = naturalCanvasSize(team);
                            // All cards use the same canvas area height
                            // for visual consistency. Each graph scales
                            // to fill the card width; the uniform height
                            // prevents some cards from looking tiny.
                            const CARD_INNER_W = 330;
                            const CANVAS_H = 200;
                            const fitScale = Math.min(
                                CARD_INNER_W / nat.width,
                                CANVAS_H / nat.height,
                                0.55,
                            );
                            const scaledW = Math.ceil(nat.width * fitScale);
                            const scaledH = Math.ceil(nat.height * fitScale);
                            return (
                                <article
                                    key={arch.id}
                                    className={`mc-compare-card${isCurrent ? " is-current" : ""}${arch.hasDriver ? "" : " is-reserved"}`}
                                    onClick={() => onExpand(arch.id)}
                                    style={{ cursor: "pointer" }}
                                    role="button"
                                    tabIndex={0}
                                    onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onExpand(arch.id); } }}
                                >
                                    <header className="mc-compare-card__head">
                                        <div className="mc-compare-card__titles">
                                            <h3 className="mc-compare-card__name">{arch.name}</h3>
                                            <p className="mc-compare-card__headline">{arch.headline}</p>
                                        </div>
                                        <div className="mc-compare-card__badges">
                                            {isCurrent && (
                                                <span className="mc-compare-badge mc-compare-badge--current">
                                                    {t("Compare_Current", "Current")}
                                                </span>
                                            )}
                                            {!arch.hasDriver && (
                                                <span
                                                    className="mc-compare-badge mc-compare-badge--reserved"
                                                    title={t("Compare_ReservedTitle", "Reserved pattern — runs on the supervisor driver until a dedicated driver ships")}
                                                >
                                                    {t("Compare_Reserved", "Preview")}
                                                </span>
                                            )}
                                        </div>
                                    </header>

                                    <div className="mc-compare-card__canvas"
                                        style={{ height: CANVAS_H }}
                                    >
                                        <div
                                            className="mc-compare-card__canvas-sizer"
                                            style={{ width: scaledW, height: scaledH }}
                                        >
                                            <div style={{
                                                transform: `scale(${fitScale})`,
                                                transformOrigin: "0 0",
                                                width: nat.width,
                                                height: nat.height,
                                            }}>
                                                <OrchCanvas team={team} showLegend={false} canvasWidth={nat.width} />
                                            </div>
                                        </div>
                                    </div>

                                    <p className="mc-compare-card__desc">{arch.description}</p>

                                    <dl className="mc-compare-card__meta">
                                        <dt>{t("Compare_PickWhen", "When to use")}</dt>
                                        <dd>{arch.pickWhen}</dd>
                                    </dl>

                                    {arch.fabricUseCases.length > 0 && (
                                        <div className="mc-compare-card__usecases">
                                            <div className="mc-compare-card__usecases-label">
                                                {t("Compare_UseCases", "Example Fabric tasks")}
                                            </div>
                                            <ul>
                                                {arch.fabricUseCases.map((u) => (
                                                    <li key={u}>{u}</li>
                                                ))}
                                            </ul>
                                        </div>
                                    )}

                                    {onPick && arch.hasDriver && !isCurrent && (
                                        <div className="mc-compare-card__actions">
                                            <button
                                                type="button"
                                                className="mc-compare-card__pick"
                                                onClick={(e) => { e.stopPropagation(); onPick(arch.id); onClose(); }}
                                            >
                                                {t("Compare_UseThis", "Use this architecture")}
                                            </button>
                                        </div>
                                    )}
                                </article>
                            );
                        })}
                    </div>
                )}

                {/* ── Expanded detail view ──────────────────────── */}
                {sorted && expandedId && (() => {
                    const arch = sorted.find((a) => a.id === expandedId);
                    if (!arch) return null;
                    const team = sampleTeamFor(arch.id);
                    const nat = naturalCanvasSize(team);
                    // Scale to fit the full dialog width (~1230 px minus padding)
                    const DETAIL_W = 1180;
                    const DETAIL_H = 480;
                    const detailScale = Math.min(
                        DETAIL_W / nat.width,
                        DETAIL_H / nat.height,
                        0.85,
                    );
                    const dw = Math.ceil(nat.width * detailScale);
                    const dh = Math.ceil(nat.height * detailScale);
                    const isCurrent = currentArchitecture === arch.id;
                    return (
                        <div className="mc-compare-detail">
                            <div className="mc-compare-detail__toolbar">
                                <button
                                    type="button"
                                    className="mc-compare-detail__back"
                                    onClick={onCollapse}
                                >
                                    <ArrowLeft20Regular />
                                    {t("Compare_BackToGrid", "All architectures")}
                                </button>
                                {onPick && arch.hasDriver && !isCurrent && (
                                    <button
                                        type="button"
                                        className="mc-compare-card__pick"
                                        onClick={() => { onPick(arch.id); onClose(); }}
                                    >
                                        {t("Compare_UseThis", "Use this architecture")}
                                    </button>
                                )}
                            </div>

                            <div className="mc-compare-detail__head">
                                <h3 className="mc-compare-detail__name">{arch.name}</h3>
                                <p className="mc-compare-detail__headline">{arch.headline}</p>
                            </div>

                            <div className="mc-compare-detail__canvas">
                                <div
                                    className="mc-compare-card__canvas-sizer"
                                    style={{ width: dw, height: dh }}
                                >
                                    <div style={{
                                        transform: `scale(${detailScale})`,
                                        transformOrigin: "0 0",
                                        width: nat.width,
                                        height: nat.height,
                                    }}>
                                        <OrchCanvas team={team} showLegend={false} canvasWidth={nat.width} />
                                    </div>
                                </div>
                            </div>

                            <div className="mc-compare-detail__info">
                                <p className="mc-compare-detail__desc">{arch.description}</p>

                                <dl className="mc-compare-detail__meta">
                                    <dt>{t("Compare_PickWhen", "When to use")}</dt>
                                    <dd>{arch.pickWhen}</dd>
                                </dl>

                                {arch.fabricUseCases.length > 0 && (
                                    <div className="mc-compare-detail__usecases">
                                        <div className="mc-compare-card__usecases-label">
                                            {t("Compare_UseCases", "Example Fabric tasks")}
                                        </div>
                                        <ul>
                                            {arch.fabricUseCases.map((u) => (
                                                <li key={u}>{u}</li>
                                            ))}
                                        </ul>
                                    </div>
                                )}
                            </div>
                        </div>
                    );
                })()}
            </div>
        </div>
    );
}
