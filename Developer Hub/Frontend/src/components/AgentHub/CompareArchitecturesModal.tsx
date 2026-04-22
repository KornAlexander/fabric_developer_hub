import React, { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Dismiss20Regular, ErrorCircle20Regular } from "@fluentui/react-icons";
import { listArchitectures, ArchitectureEntry } from "../../controller/AgentHubApi";
import { OrchCanvas } from "./team/OrchCanvas";
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
            { id: "a1", agent: "Xi — Data Engineer", role: "Specialist", status: "planned" },
        ],
        edges: [],
    }),
    supervisor: () => ({
        pattern: "supervisor",
        nodes: [
            { id: "orchestrator", agent: "Claire — Coordinator", role: "Supervisor", status: "planned" },
            { id: "a1", agent: "Xi — Data Engineer", role: "Worker", status: "planned" },
            { id: "a2", agent: "Jay — Validation Lead", role: "Worker", status: "planned" },
            { id: "a3", agent: "Dash — Power BI Expert", role: "Worker", status: "planned" },
        ],
        edges: [
            { from: "orchestrator", to: "a1", kind: "delegate" },
            { from: "orchestrator", to: "a2", kind: "delegate" },
            { from: "orchestrator", to: "a3", kind: "delegate" },
        ],
    }),
    sequential: () => ({
        pattern: "sequential",
        nodes: [
            { id: "a1", agent: "Bronze ingest", role: "Stage 1", status: "planned" },
            { id: "a2", agent: "Silver transform", role: "Stage 2", status: "planned" },
            { id: "a3", agent: "Gold model", role: "Stage 3", status: "planned" },
            { id: "a4", agent: "Publish report", role: "Stage 4", status: "planned" },
        ],
        edges: [
            { from: "a1", to: "a2", kind: "delegate" },
            { from: "a2", to: "a3", kind: "delegate" },
            { from: "a3", to: "a4", kind: "delegate" },
        ],
    }),
    parallel: () => ({
        // OrchCanvas renders "parallel" as a supervisor (hub-and-spoke)
        // layout — which matches the map-reduce story visually.
        pattern: "supervisor",
        nodes: [
            { id: "orchestrator", agent: "Claire — Coordinator", role: "Fan-out / reduce", status: "planned" },
            { id: "a1", agent: "Atlas — Worker 1", role: "Audit WS-A", status: "planned" },
            { id: "a2", agent: "Atlas — Worker 2", role: "Audit WS-B", status: "planned" },
            { id: "a3", agent: "Atlas — Worker 3", role: "Audit WS-C", status: "planned" },
        ],
        edges: [
            { from: "orchestrator", to: "a1", kind: "delegate" },
            { from: "orchestrator", to: "a2", kind: "delegate" },
            { from: "orchestrator", to: "a3", kind: "delegate" },
            { from: "a1", to: "orchestrator", kind: "report" },
            { from: "a2", to: "orchestrator", kind: "report" },
            { from: "a3", to: "orchestrator", kind: "report" },
        ],
    }),
    router: () => ({
        pattern: "supervisor",
        nodes: [
            { id: "orchestrator", agent: "Claire — Triage", role: "Router", status: "planned" },
            { id: "a1", agent: "Xi — Data Engineer", role: "Data track", status: "planned" },
            { id: "a2", agent: "Dash — Power BI Expert", role: "Reporting track", status: "planned" },
            { id: "a3", agent: "Sentinel — Security", role: "Governance track", status: "planned" },
        ],
        edges: [
            { from: "orchestrator", to: "a1", kind: "peer" },
            { from: "orchestrator", to: "a2", kind: "peer" },
            { from: "orchestrator", to: "a3", kind: "peer" },
        ],
    }),
    hierarchical: () => ({
        pattern: "hierarchical",
        nodes: [
            { id: "orchestrator", agent: "Program lead", role: "Top lead", status: "planned" },
            { id: "sl1", agent: "Ingestion lead", role: "Sub-lead", status: "planned" },
            { id: "sl2", agent: "Reporting lead", role: "Sub-lead", status: "planned" },
            { id: "w1", agent: "Xi — Data Engineer", role: "Worker", status: "planned" },
            { id: "w2", agent: "Jay — Validation Lead", role: "Worker", status: "planned" },
            { id: "w3", agent: "Dash — Power BI Expert", role: "Worker", status: "planned" },
        ],
        edges: [
            { from: "orchestrator", to: "sl1", kind: "delegate" },
            { from: "orchestrator", to: "sl2", kind: "delegate" },
            { from: "sl1", to: "w1", kind: "delegate" },
            { from: "sl1", to: "w2", kind: "delegate" },
            { from: "sl2", to: "w3", kind: "delegate" },
        ],
    }),
    reflection: () => ({
        pattern: "network",
        nodes: [
            { id: "actor", agent: "Atlas — Actor", role: "Drafts", status: "planned" },
            { id: "critic", agent: "Jay — Critic", role: "Reviews", status: "planned" },
        ],
        edges: [
            { from: "actor", to: "critic", kind: "peer" },
            { from: "critic", to: "actor", kind: "peer" },
        ],
    }),
    mixed: () => ({
        pattern: "mixed",
        nodes: [
            { id: "orchestrator", agent: "Claire — Coordinator", role: "Supervisor", status: "planned" },
            { id: "a1", agent: "Diagnostics team", role: "Parallel sub-team", status: "planned" },
            { id: "a2", agent: "Remediation pipeline", role: "Sequential sub-team", status: "planned" },
            { id: "a3", agent: "Dash — Power BI Expert", role: "Reporter", status: "planned" },
        ],
        edges: [
            { from: "orchestrator", to: "a1", kind: "delegate" },
            { from: "orchestrator", to: "a2", kind: "delegate" },
            { from: "a2", to: "a3", kind: "delegate" },
        ],
    }),
    network: () => ({
        pattern: "network",
        nodes: [
            { id: "a1", agent: "Xi — Data Engineer", role: "Peer", status: "planned" },
            { id: "a2", agent: "Dash — Power BI Expert", role: "Peer", status: "planned" },
            { id: "a3", agent: "Sentinel — Security", role: "Peer", status: "planned" },
            { id: "a4", agent: "Atlas — Analyst", role: "Peer", status: "planned" },
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
    debate: () => ({
        pattern: "network",
        nodes: [
            { id: "pro", agent: "Advocate", role: "Pro position", status: "planned" },
            { id: "con", agent: "Skeptic", role: "Con position", status: "planned" },
            { id: "judge", agent: "Judge", role: "Decides", status: "planned" },
        ],
        edges: [
            { from: "pro", to: "con", kind: "peer" },
            { from: "con", to: "pro", kind: "peer" },
            { from: "pro", to: "judge", kind: "report" },
            { from: "con", to: "judge", kind: "report" },
        ],
    }),
    magentic: () => ({
        pattern: "hierarchical",
        nodes: [
            { id: "orchestrator", agent: "Manager — Ledger", role: "Picks next agent", status: "planned" },
            { id: "a1", agent: "Xi — Data Engineer", role: "Worker", status: "planned" },
            { id: "a2", agent: "Sentinel — Security", role: "Worker", status: "planned" },
            { id: "a3", agent: "Atlas — Analyst", role: "Worker", status: "planned" },
        ],
        edges: [
            { from: "orchestrator", to: "a1", kind: "delegate" },
            { from: "orchestrator", to: "a2", kind: "delegate" },
            { from: "orchestrator", to: "a3", kind: "delegate" },
            { from: "a1", to: "orchestrator", kind: "report" },
            { from: "a2", to: "orchestrator", kind: "report" },
            { from: "a3", to: "orchestrator", kind: "report" },
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
            if (e.key === "Escape") onClose();
        };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [open, onClose]);

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
                            {t("Compare_Title") || "Compare architectures"}
                        </h2>
                        <p className="mc-compare-subtitle">
                            {t("Compare_Subtitle") || "The composer picks the architecture that best fits your task. Here's the full catalogue it chooses from."}
                        </p>
                    </div>
                    <button
                        type="button"
                        className="mc-compare-close"
                        onClick={onClose}
                        aria-label={t("Compare_Close") || "Close"}
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
                        {t("Compare_Loading") || "Loading architectures…"}
                    </div>
                )}

                {sorted && (
                    <div className="mc-compare-grid">
                        {sorted.map((arch) => {
                            const team = sampleTeamFor(arch.id);
                            const isCurrent = currentArchitecture === arch.id;
                            return (
                                <article
                                    key={arch.id}
                                    className={`mc-compare-card${isCurrent ? " is-current" : ""}${arch.hasDriver ? "" : " is-reserved"}`}
                                >
                                    <header className="mc-compare-card__head">
                                        <div className="mc-compare-card__titles">
                                            <h3 className="mc-compare-card__name">{arch.name}</h3>
                                            <p className="mc-compare-card__headline">{arch.headline}</p>
                                        </div>
                                        <div className="mc-compare-card__badges">
                                            {isCurrent && (
                                                <span className="mc-compare-badge mc-compare-badge--current">
                                                    {t("Compare_Current") || "Current"}
                                                </span>
                                            )}
                                            {!arch.hasDriver && (
                                                <span
                                                    className="mc-compare-badge mc-compare-badge--reserved"
                                                    title={t("Compare_ReservedTitle") || "Reserved pattern — runs on the supervisor driver until a dedicated driver ships"}
                                                >
                                                    {t("Compare_Reserved") || "Preview"}
                                                </span>
                                            )}
                                        </div>
                                    </header>

                                    <div className="mc-compare-card__canvas">
                                        <OrchCanvas team={team} compact showLegend={false} />
                                    </div>

                                    <p className="mc-compare-card__desc">{arch.description}</p>

                                    <dl className="mc-compare-card__meta">
                                        <dt>{t("Compare_PickWhen") || "Pick when"}</dt>
                                        <dd>{arch.pickWhen}</dd>
                                        <dt>{t("Compare_WatchFor") || "Watch for"}</dt>
                                        <dd>{arch.watchFor}</dd>
                                    </dl>

                                    {arch.fabricUseCases.length > 0 && (
                                        <div className="mc-compare-card__usecases">
                                            <div className="mc-compare-card__usecases-label">
                                                {t("Compare_UseCases") || "Example Fabric tasks"}
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
                                                onClick={() => { onPick(arch.id); onClose(); }}
                                            >
                                                {t("Compare_UseThis") || "Use this architecture"}
                                            </button>
                                        </div>
                                    )}
                                </article>
                            );
                        })}
                    </div>
                )}
            </div>
        </div>
    );
}
