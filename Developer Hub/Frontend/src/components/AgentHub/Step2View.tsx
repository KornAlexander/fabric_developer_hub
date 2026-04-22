import React, { useEffect, useRef, useState, useLayoutEffect, useMemo } from "react";
import { useTranslation } from "react-i18next";
import {
    BranchFork20Regular,
    Edit16Regular,
    Play16Filled,
    ArrowLeft16Regular,
    Flow16Regular,
    Sparkle20Regular,
    ArrowResetRegular,
    ZoomIn20Regular,
    ZoomOut20Regular,
    ArrowFit20Regular,
    FullScreenMaximize20Regular,
    Dismiss24Regular,
} from "@fluentui/react-icons";
import type { Plan, Team, TeamNode, TeamPattern } from "./plan/types";
import { TaskPromptRecap } from "./TaskPromptRecap";
import { OrchCanvas, naturalCanvasSize, agentKind, agentIcon, formatRole } from "./team/OrchCanvas";
import { CompareArchitecturesModal } from "./CompareArchitecturesModal";

/**
 * Step 2 of the New Session flow — "Proposed team & orchestration".
 *
 * Renders immediately when the user clicks "Plan this" on step 1, BEFORE
 * the LLM has responded. While waiting:
 *   • Title block shows a Sparkle icon + rotating status label
 *     ("Determining agents…", "Defining roles…", "Designing multi-agent
 *     architecture…").
 *   • Task prompt overview appears with the original prompt text typed
 *     back character-by-character (typewriter effect).
 *   • Architecture graph + roles sidebar show skeleton placeholders
 *     instead of real nodes/cards.
 *
 * Once ``plan`` arrives the skeletons smoothly swap to the real team
 * graph + role cards via a fade cross-transition. The architecture
 * canvas is wrapped in a scale-to-fit container so it never overflows
 * the viewport horizontally or triggers an extra vertical scrollbar.
 */

const LOADING_STATUSES = [
    "Determining agents…",
    "Defining roles and tasks…",
    "Designing multi-agent architecture…",
    "Checking prerequisites…",
    "Grounding in your workspace…",
];

const STATUS_ROTATE_MS = 2200;

/** Typewriter prints the given string 1 char at a time then idles.
 *  Tuned for a natural reading cadence (~28ms/char, small jitter, a
 *  brief pause on sentence breaks), with scaled step size so very long
 *  prompts still finish in a few seconds. */
function useTypewriter(text: string, speedMs = 28): { printed: string; done: boolean } {
    const [printed, setPrinted] = useState("");
    const [done, setDone] = useState(false);
    useEffect(() => {
        setPrinted("");
        setDone(false);
        if (!text) { setDone(true); return undefined; }
        // A tiny head-start so the caret doesn't sit empty for a beat.
        const initial = Math.min(text.length, 4);
        let i = initial;
        setPrinted(text.slice(0, initial));
        if (i >= text.length) { setDone(true); return undefined; }
        // Cap total typing time around ~4.5s for very long prompts by
        // scaling the per-tick step, not by speeding up each keystroke.
        const step = Math.max(1, Math.ceil((text.length - initial) / 160));
        const tick = () => {
            i = Math.min(text.length, i + step);
            if (i >= text.length) {
                setPrinted(text);
                setDone(true);
                return;
            }
            setPrinted(text.slice(0, i));
            // Small pause after sentence-ending punctuation for a
            // natural cadence; light jitter everywhere else.
            const justTyped = text.charAt(i - 1);
            const pause = /[.!?]/.test(justTyped) ? 180
                : /[,;:]/.test(justTyped) ? 90
                : 0;
            const jitter = (Math.random() * 12) - 4; // −4…+8ms
            tid = window.setTimeout(tick, speedMs + jitter + pause);
        };
        let tid = window.setTimeout(tick, speedMs);
        return () => window.clearTimeout(tid);
    }, [text, speedMs]);
    return { printed, done };
}

/** Responsive canvas sizing.
 *
 *  We let ``OrchCanvas`` always lay out at its **natural** (design-
 *  faithful) width — nodes spread out with full breathing room
 *  regardless of viewport. The wrapper then:
 *  - uses the container width directly when it's wider than natural
 *    (keeps the graph centered without empty dead space);
 *  - uniformly CSS-scales the canvas down when it's narrower (auto
 *    zoom-out) — preserves edge curves, node spacing, and legibility
 *    down to a sensible floor. Below the floor we stop scaling and
 *    let the wrapper scroll-pan so text never goes microscopic. */
const CANVAS_NATURAL_FLOOR = 640;
const CANVAS_SCALE_MIN = 0.55;
function useCanvasFit(
    containerRef: React.RefObject<HTMLElement>,
    naturalWidth: number,
): { canvasWidth: number; scale: number; scrolls: boolean; displayWidth: number } {
    const [containerW, setContainerW] = useState<number>(naturalWidth);
    useLayoutEffect(() => {
        const el = containerRef.current;
        if (!el) return undefined;
        const measure = () => {
            const w = el.clientWidth;
            if (w > 0) setContainerW(w);
        };
        measure();
        const ro = new ResizeObserver(measure);
        ro.observe(el);
        return () => ro.disconnect();
    }, [containerRef]);

    // Container wider or equal to natural: lay out at container width
    // so the graph fills the card with no scaling.
    if (containerW >= naturalWidth) {
        return {
            canvasWidth: Math.max(containerW, CANVAS_NATURAL_FLOOR),
            scale: 1,
            scrolls: false,
            displayWidth: Math.max(containerW, CANVAS_NATURAL_FLOOR),
        };
    }
    // Otherwise: lay out at natural size, zoom-out uniformly.
    const rawScale = containerW / naturalWidth;
    if (rawScale >= CANVAS_SCALE_MIN) {
        return {
            canvasWidth: naturalWidth,
            scale: rawScale,
            scrolls: false,
            // Actual post-scale width so the flex wrapper (justify:
            // center) centers the graph horizontally. Previously this
            // returned ``containerW``, which made the inner wrapper
            // fill the card and left-anchored the scaled canvas —
            // producing the asymmetric gap users saw (more empty
            // space on the left than the right).
            displayWidth: naturalWidth * rawScale,
        };
    }
    // Floor hit — keep scale at min and let the wrapper pan.
    return {
        canvasWidth: naturalWidth,
        scale: CANVAS_SCALE_MIN,
        scrolls: true,
        displayWidth: naturalWidth * CANVAS_SCALE_MIN,
    };
}

const PATTERN_META: Record<TeamPattern, { label: string; headline: (n: number) => string; subtitle: string }> = {
    supervisor: {
        label: "Supervisor pattern",
        headline: (n) => n <= 1 ? "A single orchestrator." : `A supervisor with ${n - 1} specialist${n - 1 === 1 ? "" : "s"}.`,
        subtitle: "One orchestrator coordinates the domain agents. Regenerate, swap agents, or inspect any node before anything runs.",
    },
    sequential: {
        label: "Sequential pipeline",
        headline: (n) => `A ${n}-stage pipeline.`,
        subtitle: "Each agent's output is the next agent's input. Deterministic order; no dynamic routing.",
    },
    network: {
        label: "Peer network",
        headline: (n) => `A peer mesh of ${n} specialists.`,
        subtitle: "All agents talk to each other. Any of them can hand off to any other — useful for debate, voting, and investigations.",
    },
    hierarchical: {
        label: "Hierarchical tree",
        headline: (n) => `A ${n}-agent hierarchy.`,
        subtitle: "A top-level lead delegates to sub-leads, each owning their own workers. Best for deep, multi-track tasks.",
    },
    solo: {
        label: "Solo agent",
        headline: () => "A single focused specialist.",
        subtitle: "One agent handles the entire task. Use for small, well-scoped work that doesn't need multiple skills.",
    },
    mixed: {
        label: "Mixed architecture",
        headline: (n) => `A composed team of ${n} agents.`,
        subtitle: "A top-level supervisor delegates to sub-teams that each use the pattern that fits their sub-task.",
    },
};

// "Compare architectures" button used to deep-link to the LangGraph
// docs. It now opens an in-app modal sourced from the backend catalog
// so users see *our* architecture set, in the same visual language as
// Step 2. See ``CompareArchitecturesModal``.

export interface Step2ViewProps {
    /** The prompt the user submitted on step 1 (typewritten). */
    task: string;
    /** Plan from the LLM, or null while we're still waiting. */
    plan: Plan | null;
    /** True while the LLM call is in-flight. */
    loading: boolean;
    /** Controls the Run CTA — true while the approve-and-start call is in-flight. */
    running?: boolean;
    workspaceName?: string | null;
    workspaceId?: string | null;
    workspaceItems?: Array<Record<string, unknown>> | null;
    attachments?: Array<Record<string, unknown>> | null;
    requireApprovals?: boolean;
    branchOut?: boolean;
    branchName?: string | null;
    sourceWorkspaceName?: string | null;
    onRun: () => void;
    onBack: () => void;
    /** Called when the user picks a different architecture from the
     *  "Compare architectures" modal. When omitted, the modal is
     *  read-only (no "Use this architecture" buttons). */
    onRegenerateAs?: (architectureId: string) => void;
    /** Optional error string shown inline if the LLM call failed. */
    error?: string | null;
    onRetry?: () => void;
    /** Click-to-preview handlers wired to Step 1's preview modals. */
    onItemClick?: (item: any) => void;
    onWorkspaceClick?: (ws: { id: string; name: string }) => void;
    onAttachmentClick?: (att: any) => void;
}

export function Step2View({
    task,
    plan,
    loading,
    running,
    workspaceName,
    workspaceId,
    workspaceItems,
    attachments,
    requireApprovals,
    branchOut,
    branchName,
    sourceWorkspaceName,
    onRun,
    onBack,
    onRegenerateAs,
    error,
    onRetry,
    onItemClick,
    onWorkspaceClick,
    onAttachmentClick,
}: Step2ViewProps) {
    const { t } = useTranslation();
    const { printed, done: typewriterDone } = useTypewriter(task);

    // Rotating status label while loading.
    const [statusIndex, setStatusIndex] = useState(0);
    useEffect(() => {
        if (!loading) return undefined;
        const id = window.setInterval(() => {
            setStatusIndex((i) => (i + 1) % LOADING_STATUSES.length);
        }, STATUS_ROTATE_MS);
        return () => window.clearInterval(id);
    }, [loading]);

    // ── Auto-collapse the prompt recap after a plan arrives ──
    // UX rationale: the recap is essential context while the user
    // waits for the plan (lets them verify what they submitted). Once
    // a plan lands, that context becomes visual ballast above the
    // result the user actually came to see. We wait a short beat
    // (~650ms) after loading ends so the plan's own slide-up
    // animation can settle first — a staggered reveal-then-tuck — and
    // then collapse. The recap stays fully interactive (click to
    // re-open, Edit still works) so no information is hidden from
    // the user, just de-emphasized. Regeneration (loading flips back
    // on) re-opens the recap automatically.
    const [recapOpen, setRecapOpen] = useState(true);
    useEffect(() => {
        if (loading) {
            // Re-open whenever loading kicks back in (regenerate / retry).
            setRecapOpen(true);
            return undefined;
        }
        if (!plan) return undefined;
        const id = window.setTimeout(() => setRecapOpen(false), 650);
        return () => window.clearTimeout(id);
    }, [loading, plan]);

    // Responsive canvas sizing: measure the card's inner width and let
    // ``OrchCanvas`` lay out in canvas coordinates up to that width (so
    // nodes spread out naturally, never overlap). Below a floor we keep
    // the canvas at the floor and let the wrapper scroll horizontally —
    // panning beats scaling on small viewports.
    const canvasWrapRef = useRef<HTMLDivElement | null>(null);
    const team: Team | null = plan?.team ?? null;
    // Shared hover/focus state for cross-highlighting between the
    // graph nodes and the sidebar role cards. Hovering a node in
    // either surface lights up the matching peer in the other.
    const [hoveredAgentId, setHoveredAgentId] = useState<string | null>(null);
    const pattern = (team?.pattern || "supervisor") as TeamPattern;
    const meta = PATTERN_META[pattern] || PATTERN_META.supervisor;
    const agentCount = team?.nodes.length || 0;
    // Count unique unordered endpoint pairs rather than raw edges, so
    // a delegate (A→B) + report (B→A) round-trip reports as ONE
    // connection — matching what the canvas actually renders after
    // OrchCanvas merges round-trip edges into a single curve.
    const connectionCount = useMemo(() => {
        if (!team) return 0;
        const seen = new Set<string>();
        for (const e of team.edges) seen.add([e.from, e.to].sort().join("↔"));
        return seen.size;
    }, [team]);
    const naturalSize = useMemo(
        () => team ? naturalCanvasSize(team) : { width: CANVAS_NATURAL_FLOOR, height: 420 },
        [team],
    );
    const { canvasWidth, scale, scrolls } = useCanvasFit(canvasWrapRef, naturalSize.width);

    // ── User-controlled zoom + pan ────────────────────────────────
    // Google-Maps / Figma-style pan+zoom: instead of piggybacking on
    // native scroll (which has an awkward "fits vs overflows" boundary
    // because margin:auto centers on fit and collapses on overflow),
    // we apply a single CSS transform — ``translate(tx,ty) scale(S)`` —
    // to the content stage inside a fixed ``overflow: hidden`` viewport.
    //
    // With this approach the cursor-anchor math is exact and simple:
    //
    //   screen = tx + world * scale
    //   world-under-cursor = (screenFocal - tx) / scale
    //
    // To keep that world point stationary as the scale goes s→s',
    // solve for the new tx:
    //
    //   tx' = screenFocal - (screenFocal - tx) * (s'/s)
    //       = screenFocal * (1 - s'/s) + tx * (s'/s)
    //
    // No state capture + layout-effect dance, no flex-center fighting,
    // no scroll clamping edge cases. Same formula used by Google Maps,
    // Figma, Miro, Lucidchart.
    const USER_ZOOM_MIN = 0.4;
    const USER_ZOOM_MAX = 2.5;
    const USER_ZOOM_STEP_WHEEL = 0.08;
    const USER_ZOOM_STEP_BUTTON = 0.15;
    const [userZoom, setUserZoom] = useState(1);
    const [pan, setPan] = useState<{ x: number; y: number } | null>(null);
    const canvasScrollRef = useRef<HTMLDivElement | null>(null);
    const canvasInnerRef = useRef<HTMLDivElement | null>(null);
    // Actual rendered content height (pre-transform). Measured after
    // mount because nodes with many selected skills, long roles, or
    // wrapped summaries render much taller than the static
    // ``NODE_CONTENT_H`` budget baked into ``naturalCanvasSize``. We
    // use this true height so the initial fit-to-view centers on the
    // real bounding box instead of clipping rows.
    const [measuredH, setMeasuredH] = useState<number>(naturalSize.height);
    // Observed viewport height — ``useCanvasFit`` measures width, but
    // fit-to-view must consider both axes or a tall hierarchical tree
    // overflows the top of the stage when the width-fit scale is too
    // generous. We keep a separate observer tied to the same element
    // so ``scale`` and ``measuredH`` combine into a true "fit" below.
    const [measuredViewportH, setMeasuredViewportH] = useState<number>(0);
    useLayoutEffect(() => {
        const el = canvasScrollRef.current;
        if (!el) return undefined;
        const read = () => setMeasuredViewportH(el.clientHeight);
        read();
        const ro = new ResizeObserver(read);
        ro.observe(el);
        return () => ro.disconnect();
    }, []);
    useLayoutEffect(() => {
        const el = canvasInnerRef.current;
        if (!el) return undefined;
        // The ``.mc-canvas`` box inside carries an explicit height from
        // the layout math (based on static ``NODE_CONTENT_H``), but
        // absolutely-positioned node children can render well past
        // that — long roles, many highlighted skill chips, or wrapped
        // summaries commonly push nodes from the 188px budget to
        // 260–360px. We scan the node children and take the max
        // bottom edge so fit-to-view is based on what the user can
        // actually see, not what the layout thinks it drew.
        const read = () => {
            let maxBottom = el.offsetHeight;
            const nodes = el.querySelectorAll<HTMLElement>(".mc-node");
            nodes.forEach((n) => {
                const bottom = n.offsetTop + n.offsetHeight;
                if (bottom > maxBottom) maxBottom = bottom;
            });
            // Add a small bottom gutter so the last row doesn't butt
            // the viewport edge on initial fit.
            setMeasuredH(Math.max(naturalSize.height, maxBottom + 24));
        };
        read();
        const ro = new ResizeObserver(read);
        ro.observe(el);
        el.querySelectorAll<HTMLElement>(".mc-node").forEach((n) => ro.observe(n));
        return () => ro.disconnect();
    }, [naturalSize.height, team]);
    // Height-aware fit correction. ``scale`` from ``useCanvasFit`` only
    // considers width, so a hierarchical tree — taller than wide —
    // overflowed the top of the stage at the default zoom, clipping
    // the root node. We multiply in a "height fit" ratio when the
    // content would overflow vertically at ``scale``, so the
    // effective fit scale honours both axes.
    const heightFitMultiplier = useMemo(() => {
        if (!measuredViewportH || measuredH <= 0 || scale <= 0) return 1;
        const displayH = measuredH * scale;
        if (displayH <= measuredViewportH) return 1;
        return measuredViewportH / displayH;
    }, [measuredH, scale, measuredViewportH]);
    const autoFitScale = scale * heightFitMultiplier;
    const effectiveScale = autoFitScale * userZoom;
    const effectiveDisplayW = canvasWidth * effectiveScale;
    const effectiveDisplayH = measuredH * effectiveScale;
    const isInteractive = userZoom !== 1 || scrolls;

    // Initial/recenter pan — whenever the viewport size, the natural
    // canvas size, or the fit-scale changes we re-center the stage so
    // a freshly-rendered graph lands visually centered. We only force
    // this on the derived-size dependencies; user-driven pan (from drag
    // or zoom) does NOT get overwritten because userZoom isn't in deps.
    useLayoutEffect(() => {
        const el = canvasScrollRef.current;
        if (!el) return;
        const vw = el.clientWidth;
        const vh = el.clientHeight;
        const cx = (vw - effectiveDisplayW) / 2;
        const cy = (vh - effectiveDisplayH) / 2;
        setPan({ x: cx, y: cy });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [canvasWidth, measuredH, autoFitScale]);
    // (effectiveDisplayW/H are derived from the above — no need to add.)

    /** Zoom while keeping ``focal`` (in viewport-local px) stationary
     *  on the world. Pass null to anchor on viewport center. */
    const zoomAt = (delta: number, focal: { x: number; y: number } | null) => {
        const el = canvasScrollRef.current;
        if (!el) return;
        const rect = { w: el.clientWidth, h: el.clientHeight };
        const fx = focal ? focal.x : rect.w / 2;
        const fy = focal ? focal.y : rect.h / 2;
        setUserZoom((z) => {
            const nextZ = Math.max(USER_ZOOM_MIN, Math.min(USER_ZOOM_MAX, z + delta));
            const rounded = Math.round(nextZ * 100) / 100;
            if (rounded === z) return z;
            const prevEff = autoFitScale * z;
            const nextEff = autoFitScale * rounded;
            const r = nextEff / prevEff;
            setPan((p) => {
                const px = p ? p.x : 0;
                const py = p ? p.y : 0;
                return {
                    x: fx * (1 - r) + px * r,
                    y: fy * (1 - r) + py * r,
                };
            });
            return rounded;
        });
    };
    const zoomBy = (delta: number) => zoomAt(delta, null);
    const resetZoom = () => {
        // Reset both the user zoom and the pan so the graph
        // re-centers on the viewport. Equivalent to "fit to view".
        setUserZoom(1);
        const el = canvasScrollRef.current;
        if (el) {
            const fitW = canvasWidth * autoFitScale;
            const fitH = measuredH * autoFitScale;
            setPan({
                x: (el.clientWidth - fitW) / 2,
                y: (el.clientHeight - fitH) / 2,
            });
        }
    };

    // Wheel zoom — cursor-anchored, no modifier key required.
    useEffect(() => {
        const el = canvasScrollRef.current;
        if (!el) return undefined;
        const onWheel = (e: WheelEvent) => {
            // Horizontal-dominant gestures pan the world instead of
            // zooming (natural on trackpads with 2-finger pan).
            const rect = el.getBoundingClientRect();
            if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
                e.preventDefault();
                setPan((p) => ({
                    x: (p?.x || 0) - e.deltaX,
                    y: (p?.y || 0) - e.deltaY,
                }));
                return;
            }
            e.preventDefault();
            const focal = { x: e.clientX - rect.left, y: e.clientY - rect.top };
            const magnitude = Math.min(1, Math.abs(e.deltaY) / 100);
            const signed = e.deltaY < 0 ? USER_ZOOM_STEP_WHEEL : -USER_ZOOM_STEP_WHEEL;
            zoomAt(signed * (0.5 + magnitude), focal);
        };
        el.addEventListener("wheel", onWheel, { passive: false });
        return () => el.removeEventListener("wheel", onWheel);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [scale]);

    // Click-drag panning — mutates the transform pan directly so the
    // cursor stays glued to the world point under it while dragging.
    useEffect(() => {
        const el = canvasScrollRef.current;
        if (!el || !isInteractive) return undefined;
        let down = false;
        let startX = 0, startY = 0, startPan = { x: 0, y: 0 };
        const onDown = (e: MouseEvent) => {
            const target = e.target as HTMLElement;
            if (target.closest("button, a, .mc-node__skill--more")) return;
            down = true;
            startX = e.clientX; startY = e.clientY;
            startPan = pan ? { ...pan } : { x: 0, y: 0 };
            el.classList.add("mc-canvas-fit--grabbing");
        };
        const onMove = (e: MouseEvent) => {
            if (!down) return;
            setPan({
                x: startPan.x + (e.clientX - startX),
                y: startPan.y + (e.clientY - startY),
            });
        };
        const onUp = () => {
            down = false;
            el.classList.remove("mc-canvas-fit--grabbing");
        };
        el.addEventListener("mousedown", onDown);
        window.addEventListener("mousemove", onMove);
        window.addEventListener("mouseup", onUp);
        return () => {
            el.removeEventListener("mousedown", onDown);
            window.removeEventListener("mousemove", onMove);
            window.removeEventListener("mouseup", onUp);
        };
    }, [isInteractive, pan]);

    // "Compare architectures" modal state.
    const [compareOpen, setCompareOpen] = useState(false);

    // Full-screen team composition modal state. Opens a lightweight
    // overlay that renders the same OrchCanvas at its natural layout
    // size so large pipelines / meshes can be inspected without
    // squeezing into the card. Escape + backdrop click close; Escape
    // handling lives on the modal itself.
    const [fullscreenOpen, setFullscreenOpen] = useState(false);

    const headline = loading || !team
        ? (t("Step2_Loading_Title") || "Designing your team…")
        : meta.headline(agentCount);
    const subtitle = loading || !team
        ? LOADING_STATUSES[statusIndex]
        : meta.subtitle;

    const orchestrator = team?.nodes.find((n) => n.id === "orchestrator") || team?.nodes[0];
    const workers = team ? team.nodes.filter((n) => n.id !== orchestrator?.id) : [];
    const orderedRoles = orchestrator && team ? [orchestrator, ...workers] : [];

    return (
        <section className="mc-review mc-step2" aria-labelledby="mc-step2-title" aria-busy={loading}>
            {/* ── Title block ─────────────────────────────────────── */}
            <header className="mc-review__header mc-slide-up">
                <div style={{ minWidth: 0 }}>
                    <div className="mc-review__eyebrow">
                        {loading ? <Sparkle20Regular className="mc-step2__spark" /> : <Flow16Regular />}
                        {t("Review_Eyebrow") || "Proposed team & orchestration"}
                    </div>
                    <h1 className="mc-review__h1" id="mc-step2-title">
                        {loading || !team ? (
                            <span className="mc-step2__headline-loading">
                                <span className="mc-step2__headline-dot mc-step2__headline-dot--1" />
                                <span className="mc-step2__headline-dot mc-step2__headline-dot--2" />
                                <span className="mc-step2__headline-dot mc-step2__headline-dot--3" />
                                <span>{headline}</span>
                            </span>
                        ) : headline}
                    </h1>
                    <p className="mc-review__subtitle mc-step2__subtitle" key={subtitle}>
                        {subtitle}
                    </p>
                </div>
                <div className="mc-review__actions">
                    <button
                        type="button"
                        className="mc-review__action mc-review__action--primary"
                        onClick={() => setCompareOpen(true)}
                        title={t("Review_CompareTitle") || "See every architecture the composer can pick, with example graphs and use cases"}
                    >
                        <BranchFork20Regular />
                        {t("Review_Compare") || "Compare architectures"}
                    </button>
                    <button
                        type="button"
                        className="mc-review__action"
                        onClick={onBack}
                        disabled={running}
                    >
                        <Edit16Regular />
                        {t("Review_EditAgents") || "Edit task"}
                    </button>
                </div>
            </header>

            {/* ── Task prompt overview (typewriter while loading) ── */}
            <div className="mc-slide-up mc-slide-up--delay-1">
                <TaskPromptRecap
                    task={loading && !typewriterDone ? printed + "▍" : (task || printed)}
                    workspaceName={workspaceName}
                    workspaceId={workspaceId}
                    workspaceItems={workspaceItems as any}
                    attachments={attachments as any}
                    requireApprovals={requireApprovals}
                    branchOut={branchOut}
                    branchName={branchName}
                    sourceWorkspaceName={sourceWorkspaceName}
                    onEdit={onBack}
                    onItemClick={onItemClick}
                    onWorkspaceClick={onWorkspaceClick}
                    onAttachmentClick={onAttachmentClick}
                    open={recapOpen}
                    onOpenChange={setRecapOpen}
                />
            </div>

            {/* ── Error state (LLM failed) ────────────────────────── */}
            {error && !loading && !plan && (
                <div className="mc-step2__error mc-slide-up mc-slide-up--delay-2">
                    <strong>{t("Step2_Error_Title") || "We couldn't generate a plan."}</strong>
                    <span>{error}</span>
                    {onRetry && (
                        <button type="button" className="mc-review__action" onClick={onRetry}>
                            <ArrowResetRegular />
                            {t("Step2_Retry") || "Try again"}
                        </button>
                    )}
                </div>
            )}

            {/* ── Split panel: graph + roles sidebar ──────────────── */}
            {/* Both panels share the same header shape (h3 title +
               supporting meta chip/line) so they read as peers. No
               redundant step-level supertitles here — the top header
               ("PROPOSED TEAM & ORCHESTRATION") already frames this. */}
            <div className="mc-split-panel mc-slide-up mc-slide-up--delay-2">
                <div className="mc-canvas-card">
                    <div className="mc-canvas-card__head">
                        <div className="mc-canvas-card__head-left">
                            <h3 className="mc-section-title">
                                {t("Review_TeamComposition") || "Team composition"}
                            </h3>
                            {team && (
                                <span className="mc-pill mc-pill--planned">{meta.label}</span>
                            )}
                            <span className="mc-section-meta">
                                {team
                                    ? `${agentCount} agent${agentCount === 1 ? "" : "s"} · ${connectionCount} connection${connectionCount === 1 ? "" : "s"}`
                                    : null}
                            </span>
                        </div>
                        {team && (
                            <div className="mc-canvas-card__tools" role="group" aria-label={t("Review_Canvas_ZoomControls") || "Canvas zoom"}>
                                <button
                                    type="button"
                                    className="mc-canvas-card__tool"
                                    onClick={() => zoomBy(-USER_ZOOM_STEP_BUTTON)}
                                    disabled={userZoom <= USER_ZOOM_MIN + 0.001}
                                    title={t("Review_Canvas_ZoomOut") || "Zoom out"}
                                    aria-label={t("Review_Canvas_ZoomOut") || "Zoom out"}
                                >
                                    <ZoomOut20Regular />
                                </button>
                                <button
                                    type="button"
                                    className="mc-canvas-card__tool mc-canvas-card__tool--reset"
                                    onClick={resetZoom}
                                    title={t("Review_Canvas_FitToScreen") || "Fit to view"}
                                    aria-label={t("Review_Canvas_FitToScreen") || "Fit to view"}
                                >
                                    <ArrowFit20Regular />
                                    <span className="mc-canvas-card__tool-label">{Math.round(effectiveScale * 100)}%</span>
                                </button>
                                <button
                                    type="button"
                                    className="mc-canvas-card__tool"
                                    onClick={() => zoomBy(USER_ZOOM_STEP_BUTTON)}
                                    disabled={userZoom >= USER_ZOOM_MAX - 0.001}
                                    title={t("Review_Canvas_ZoomIn") || "Zoom in"}
                                    aria-label={t("Review_Canvas_ZoomIn") || "Zoom in"}
                                >
                                    <ZoomIn20Regular />
                                </button>
                                <button
                                    type="button"
                                    className="mc-canvas-card__tool mc-canvas-card__tool--expand"
                                    onClick={() => setFullscreenOpen(true)}
                                    title={t("Review_Canvas_Expand") || "Open full view"}
                                    aria-label={t("Review_Canvas_Expand") || "Open full view"}
                                >
                                    <FullScreenMaximize20Regular />
                                </button>
                            </div>
                        )}
                    </div>

                    {/* Responsive canvas container: OrchCanvas lays
                        out at its natural (design-faithful) width,
                        and we apply a single CSS transform —
                        ``translate(tx,ty) scale(S)`` — to pan+zoom
                        around that fixed-size "world" inside a
                        clipped viewport. Google-Maps / Figma style. */}
                    <div
                        className={`mc-canvas-fit${isInteractive ? " mc-canvas-fit--scrolls" : ""}`}
                        ref={(el) => { canvasWrapRef.current = el; canvasScrollRef.current = el; }}
                    >
                        {team ? (
                            <div
                                className="mc-canvas-fit__inner"
                                ref={canvasInnerRef}
                                style={{
                                    width: canvasWidth,
                                    /* No explicit height — let the
                                       stage size to its real content
                                       so offsetHeight reads true. */
                                    transform: `translate(${pan?.x || 0}px, ${pan?.y || 0}px) scale(${effectiveScale})`,
                                    transformOrigin: "0 0",
                                }}
                            >
                                <OrchCanvas
                                    team={team}
                                    showLegend={false}
                                    canvasWidth={canvasWidth}
                                    activeAgentId={hoveredAgentId}
                                    onNodeHover={setHoveredAgentId}
                                />
                            </div>
                        ) : (
                            <CanvasSkeleton canvasWidth={canvasWidth} />
                        )}
                    </div>

                    {/* The canvas owns its own inline status key +
                        per-edge labels now (see OrchCanvas), so no
                        standalone legend block here — eyes stay on
                        the graph instead of hopping to a legend. */}
                </div>

                <aside className="mc-sidebar">
                    <div>
                        <div className="mc-sidebar__eyebrow">
                            {(t("Review_Eyebrow") || "Agents in this run")}
                        </div>
                        <h3 className="mc-section-title mc-sidebar__h2">
                            {t("Review_Roles") || "Roles & handoffs"}
                        </h3>
                        {team && (
                            <span className="mc-section-meta">
                                {`${agentCount} agent${agentCount === 1 ? "" : "s"}`}
                            </span>
                        )}
                    </div>

                    <div className="mc-sidebar__roles">
                        {loading || !team
                            ? Array.from({ length: 4 }).map((_, i) => <RoleCardSkeleton key={i} delay={i * 120} />)
                            : orderedRoles.map((n) => {
                                const kind = agentKind(n.agent, n.id);
                                const summary = n.summary || roleSummary(n, team, workers.length);
                                return (
                                <article
                                    key={n.id}
                                    className="mc-role-card mc-slide-up"
                                    data-active={hoveredAgentId === n.id ? "true" : undefined}
                                    onMouseEnter={() => setHoveredAgentId(n.id)}
                                    onMouseLeave={() => setHoveredAgentId(null)}
                                    onFocus={() => setHoveredAgentId(n.id)}
                                    onBlur={() => setHoveredAgentId(null)}
                                    tabIndex={0}
                                >
                                    <div className="mc-role-card__head">
                                        <span className="mc-role-card__icon" data-agent={kind} aria-hidden="true">
                                            {agentIcon(kind)}
                                        </span>
                                        <div style={{ minWidth: 0 }}>
                                            <div className="mc-role-card__name">{n.agent}</div>
                                            <div className="mc-role-card__role">{formatRole(n.role)}</div>
                                        </div>
                                    </div>
                                    {summary && (
                                        <p className="mc-role-card__desc">{summary}</p>
                                    )}
                                </article>
                                );
                            })}
                    </div>

                    <button
                        type="button"
                        className="mc-review__cta-primary"
                        onClick={onRun}
                        disabled={running || loading || !plan}
                    >
                        <Play16Filled />
                        {loading
                            ? (t("Step2_WaitingForPlan") || "Waiting for plan…")
                            : running ? (t("Review_Running") || "Starting run…") : (t("Review_RunCta") || "Run this orchestration")}
                    </button>
                    <button
                        type="button"
                        className="mc-review__cta-secondary"
                        onClick={onBack}
                        disabled={running}
                    >
                        <ArrowLeft16Regular />
                        {t("Review_BackCta") || "Back — edit task"}
                    </button>
                </aside>
            </div>

            <CompareArchitecturesModal
                open={compareOpen}
                onClose={() => setCompareOpen(false)}
                currentArchitecture={pattern}
            />

            {fullscreenOpen && team && (
                <TeamCompositionFullscreen
                    team={team}
                    meta={meta}
                    agentCount={agentCount}
                    connectionCount={connectionCount}
                    onClose={() => setFullscreenOpen(false)}
                />
            )}
        </section>
    );
}

/* ── Helpers & skeletons ─────────────────────────────────────── */

/**
 * Render a natural-language blurb describing what an agent does in the
 * current run — the paragraph shown under each role card in the
 * Step 2 sidebar. When the backend supplies ``TeamNode.summary`` we
 * use that directly; this fallback keeps the design-accurate prose
 * shape even for legacy plans that don't include a summary.
 */
function roleSummary(node: TeamNode, team: Team | null, workerCount: number): string {
    if (!team) return "";
    const isOrchestrator = node.id === "orchestrator";
    if (isOrchestrator) {
        const n = Math.max(1, workerCount);
        const workstreams = n === 1 ? "the workstream" : `${n} workstreams`;
        return `Splits the task into ${workstreams}, delegates each, and gates approvals to you.`;
    }
    const outbound = team.edges
        .filter((e) => e.from === node.id && e.kind !== "report")
        .map((e) => team.nodes.find((x) => x.id === e.to)?.agent)
        .filter((x): x is string => Boolean(x));
    // Normalize the role text: backend often supplies it in uppercase
    // with a trailing period (display-friendly for the node card's
    // eyebrow line, but awkward when spliced into a sentence). Strip
    // both so we produce "Handles transform the data, then hands off…"
    // instead of "Handles TRANSFORM THE DATA. for this run."
    const role = node.role.toLowerCase().replace(/[.!?\s]+$/, "").trim();
    const handoffClause = outbound.length > 0
        ? `, then hands off to ${outbound.join(" and ")}.`
        : ".";
    return `Handles ${role}${handoffClause}`;
}

function RoleCardSkeleton({ delay = 0 }: { delay?: number }) {
    return (
        <article className="mc-role-card mc-role-card--skeleton" style={{ animationDelay: `${delay}ms` }}>
            <div className="mc-role-card__head">
                <span className="mc-skeleton mc-skeleton--avatar" />
                <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 4 }}>
                    <span className="mc-skeleton mc-skeleton--line mc-skeleton--line-60" />
                    <span className="mc-skeleton mc-skeleton--line mc-skeleton--line-40" />
                </div>
            </div>
        </article>
    );
}

function CanvasSkeleton({ canvasWidth }: { canvasWidth: number }) {
    // Supervisor-shaped skeleton (1 lead + 3 workers) sized to the
    // current canvas width so the swap to the real graph is visually
    // stable — matching placement, matching breathing room.
    const W = canvasWidth;
    const H = 480;
    const NODE_W = 200;
    const orchX = (W - NODE_W) / 2;
    const workerY = 320;
    const gutter = 32;
    const rowW = 3 * NODE_W + 2 * gutter;
    const workerStart = (W - rowW) / 2;
    const workerXs = [0, 1, 2].map((i) => workerStart + i * (NODE_W + gutter));
    return (
        <div className="mc-canvas-fit__skeleton" style={{ width: W }}>
            <div className="mc-canvas mc-canvas--skeleton" style={{ width: W, height: H }}>
                <svg className="mc-canvas__edges" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden="true">
                    {workerXs.map((wx, i) => {
                        const x1 = orchX + NODE_W / 2;
                        const y1 = 40 + 110;
                        const x2 = wx + NODE_W / 2;
                        const y2 = workerY;
                        const midY = (y1 + y2) / 2;
                        return (
                            <path
                                key={i}
                                className="mc-edge"
                                data-kind="delegate"
                                d={`M ${x1} ${y1} C ${x1} ${midY}, ${x2} ${midY}, ${x2} ${y2}`}
                            />
                        );
                    })}
                </svg>
                <div className="mc-node mc-node--skeleton" data-state="planned" style={{ left: orchX, top: 40, width: NODE_W }}>
                    <div className="mc-node__head">
                        <span className="mc-skeleton mc-skeleton--avatar" />
                        <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 4 }}>
                            <span className="mc-skeleton mc-skeleton--line mc-skeleton--line-70" />
                            <span className="mc-skeleton mc-skeleton--line mc-skeleton--line-40" />
                        </div>
                    </div>
                </div>
                {workerXs.map((left, i) => (
                    <div
                        key={i}
                        className="mc-node mc-node--skeleton"
                        data-state="planned"
                        style={{ left, top: workerY, width: NODE_W, animationDelay: `${i * 150}ms` }}
                    >
                        <div className="mc-node__head">
                            <span className="mc-skeleton mc-skeleton--avatar" />
                            <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 4 }}>
                                <span className="mc-skeleton mc-skeleton--line mc-skeleton--line-70" />
                                <span className="mc-skeleton mc-skeleton--line mc-skeleton--line-40" />
                            </div>
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
}

/**
 * Full-screen modal view of the team composition graph. Mirrors the
 * visual shape of ``WorkspacePreviewModal`` / attachment preview
 * (``attachment-preview-backdrop`` + dialog) so the experience is
 * consistent across previews.
 *
 * The modal fits the graph to its own viewport (90vw × 86vh) and
 * supports the same zoom toolbar + wheel-zoom + drag-to-pan as the
 * inline canvas — all at a much roomier size.
 */
function TeamCompositionFullscreen({
    team,
    meta,
    agentCount,
    connectionCount,
    onClose,
}: {
    team: Team;
    meta: { label: string };
    agentCount: number;
    connectionCount: number;
    onClose: () => void;
}) {
    const { t } = useTranslation();
    const natural = useMemo(() => naturalCanvasSize(team), [team]);
    const wrapRef = useRef<HTMLDivElement | null>(null);
    const innerRef = useRef<HTMLDivElement | null>(null);
    // Auto-fit on open; then the user can zoom freely.
    const [containerSize, setContainerSize] = useState({ w: natural.width, h: natural.height });
    useLayoutEffect(() => {
        const el = wrapRef.current;
        if (!el) return undefined;
        const measure = () => setContainerSize({ w: el.clientWidth, h: el.clientHeight });
        measure();
        const ro = new ResizeObserver(measure);
        ro.observe(el);
        return () => ro.disconnect();
    }, []);
    // Measured content bounds — nodes can render significantly taller
    // than the static ``NODE_CONTENT_H`` budget because of multi-line
    // roles, multiple selected skill chips, and wrapped descriptions.
    // Using the max ``offsetTop + offsetHeight`` of any rendered node
    // gives us the true content bottom so fit-to-view centers on
    // what the user sees (not on the layout's internal guess).
    const [contentSize, setContentSize] = useState({ w: natural.width, h: natural.height });
    useLayoutEffect(() => {
        const el = innerRef.current;
        if (!el) return undefined;
        const read = () => {
            let maxBottom = el.offsetHeight;
            let maxRight = el.offsetWidth;
            const nodes = el.querySelectorAll<HTMLElement>(".mc-node");
            nodes.forEach((n) => {
                const bottom = n.offsetTop + n.offsetHeight;
                const right = n.offsetLeft + n.offsetWidth;
                if (bottom > maxBottom) maxBottom = bottom;
                if (right > maxRight) maxRight = right;
            });
            setContentSize({
                w: Math.max(natural.width, maxRight + 24),
                h: Math.max(natural.height, maxBottom + 24),
            });
        };
        read();
        const ro = new ResizeObserver(read);
        ro.observe(el);
        el.querySelectorAll<HTMLElement>(".mc-node").forEach((n) => ro.observe(n));
        return () => ro.disconnect();
    }, [natural.width, natural.height, team]);
    const fitScale = useMemo(() => {
        const sW = containerSize.w / contentSize.w;
        const sH = containerSize.h / contentSize.h;
        // Never upscale past natural in the fullscreen view — the
        // purpose is to *fit* the graph, not stretch it onto a 4K
        // monitor. Users who want zoom-in can still push the
        // toolbar. We also cap at 0.9 so even a graph that fits
        // comfortably gets a visible margin around it instead of
        // butting against the stage edges (the user wants a
        // "image-1-style" centered look by default).
        return Math.min(0.9, Math.max(0.3, Math.min(sW, sH)));
    }, [containerSize, contentSize]);
    const [userZoom, setUserZoom] = useState(1);
    const [pan, setPan] = useState<{ x: number; y: number } | null>(null);
    const effective = fitScale * userZoom;
    const ZMIN = 0.3, ZMAX = 3, ZSTEP_WHEEL = 0.08, ZSTEP_BUTTON = 0.15;
    // Re-center whenever container or content size changes. User-driven
    // pan/zoom is preserved (deps list intentionally excludes userZoom).
    useLayoutEffect(() => {
        if (!containerSize.w || !containerSize.h) return;
        const w = contentSize.w * fitScale;
        const h = contentSize.h * fitScale;
        setPan({
            x: (containerSize.w - w) / 2,
            y: (containerSize.h - h) / 2,
        });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [containerSize.w, containerSize.h, contentSize.w, contentSize.h, fitScale]);

    /** Cursor-anchored zoom via single transform. */
    const zoomAt = (delta: number, focal: { x: number; y: number } | null) => {
        const el = wrapRef.current;
        if (!el) return;
        const fx = focal ? focal.x : el.clientWidth / 2;
        const fy = focal ? focal.y : el.clientHeight / 2;
        setUserZoom((z) => {
            const nextZ = Math.round(Math.max(ZMIN, Math.min(ZMAX, z + delta)) * 100) / 100;
            if (nextZ === z) return z;
            const r = (fitScale * nextZ) / (fitScale * z);
            setPan((p) => ({
                x: fx * (1 - r) + (p?.x || 0) * r,
                y: fy * (1 - r) + (p?.y || 0) * r,
            }));
            return nextZ;
        });
    };
    const zoomBy = (d: number) => zoomAt(d, null);
    const resetZoom = () => {
        setUserZoom(1);
        setPan({
            x: (containerSize.w - contentSize.w * fitScale) / 2,
            y: (containerSize.h - contentSize.h * fitScale) / 2,
        });
    };

    // Close on Escape.
    useEffect(() => {
        const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [onClose]);

    // Wheel zoom — cursor-anchored.
    useEffect(() => {
        const el = wrapRef.current;
        if (!el) return undefined;
        const onWheel = (e: WheelEvent) => {
            const rect = el.getBoundingClientRect();
            if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
                e.preventDefault();
                setPan((p) => ({ x: (p?.x || 0) - e.deltaX, y: (p?.y || 0) - e.deltaY }));
                return;
            }
            e.preventDefault();
            const focal = { x: e.clientX - rect.left, y: e.clientY - rect.top };
            const magnitude = Math.min(1, Math.abs(e.deltaY) / 100);
            const signed = e.deltaY < 0 ? ZSTEP_WHEEL : -ZSTEP_WHEEL;
            zoomAt(signed * (0.5 + magnitude), focal);
        };
        el.addEventListener("wheel", onWheel, { passive: false });
        return () => el.removeEventListener("wheel", onWheel);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [fitScale]);
    useEffect(() => {
        const el = wrapRef.current;
        if (!el) return undefined;
        let down = false;
        let startX = 0, startY = 0, startPan = { x: 0, y: 0 };
        const onDown = (e: MouseEvent) => {
            const target = e.target as HTMLElement;
            if (target.closest("button, a, .mc-node__skill--more")) return;
            down = true;
            startX = e.clientX; startY = e.clientY;
            startPan = pan ? { ...pan } : { x: 0, y: 0 };
            el.classList.add("mc-canvas-fit--grabbing");
        };
        const onMove = (e: MouseEvent) => {
            if (!down) return;
            setPan({
                x: startPan.x + (e.clientX - startX),
                y: startPan.y + (e.clientY - startY),
            });
        };
        const onUp = () => {
            down = false;
            el.classList.remove("mc-canvas-fit--grabbing");
        };
        el.addEventListener("mousedown", onDown);
        window.addEventListener("mousemove", onMove);
        window.addEventListener("mouseup", onUp);
        return () => {
            el.removeEventListener("mousedown", onDown);
            window.removeEventListener("mousemove", onMove);
            window.removeEventListener("mouseup", onUp);
        };
    }, [pan]);

    return (
        <div
            className="attachment-preview-backdrop"
            onClick={onClose}
            role="presentation"
        >
            <div
                className="attachment-preview-dialog mc-team-fullscreen"
                onClick={(e) => e.stopPropagation()}
                role="dialog"
                aria-modal="true"
                aria-label={t("Review_Canvas_Expand") || "Team composition full view"}
            >
                <header className="attachment-preview-head">
                    <span className="attachment-preview-name">
                        <Flow16Regular aria-hidden />
                        <span>{t("Review_TeamComposition") || "Team composition"}</span>
                        <span className="mc-pill mc-pill--planned" style={{ marginLeft: 8 }}>{meta.label}</span>
                        <span className="mc-section-meta" style={{ marginLeft: 8 }}>
                            {`${agentCount} agent${agentCount === 1 ? "" : "s"} · ${connectionCount} connection${connectionCount === 1 ? "" : "s"}`}
                        </span>
                    </span>
                    <div className="workspace-preview-head-actions">
                        <div className="mc-canvas-card__tools" role="group" aria-label={t("Review_Canvas_ZoomControls") || "Canvas zoom"}>
                            <button
                                type="button"
                                className="mc-canvas-card__tool"
                                onClick={() => zoomBy(-ZSTEP_BUTTON)}
                                disabled={userZoom <= ZMIN + 0.001}
                                title={t("Review_Canvas_ZoomOut") || "Zoom out"}
                                aria-label={t("Review_Canvas_ZoomOut") || "Zoom out"}
                            >
                                <ZoomOut20Regular />
                            </button>
                            <button
                                type="button"
                                className="mc-canvas-card__tool mc-canvas-card__tool--reset"
                                onClick={resetZoom}
                                title={t("Review_Canvas_FitToScreen") || "Fit to view"}
                                aria-label={t("Review_Canvas_FitToScreen") || "Fit to view"}
                            >
                                <ArrowFit20Regular />
                                <span className="mc-canvas-card__tool-label">{Math.round(effective * 100)}%</span>
                            </button>
                            <button
                                type="button"
                                className="mc-canvas-card__tool"
                                onClick={() => zoomBy(ZSTEP_BUTTON)}
                                disabled={userZoom >= ZMAX - 0.001}
                                title={t("Review_Canvas_ZoomIn") || "Zoom in"}
                                aria-label={t("Review_Canvas_ZoomIn") || "Zoom in"}
                            >
                                <ZoomIn20Regular />
                            </button>
                        </div>
                        <button
                            type="button"
                            className="attachment-preview-close"
                            onClick={onClose}
                            aria-label={t("Common_Close") || "Close"}
                            title={t("Common_Close") || "Close"}
                        >
                            <Dismiss24Regular />
                        </button>
                    </div>
                </header>
                <div
                    className="mc-team-fullscreen__stage mc-canvas-fit mc-canvas-fit--scrolls"
                    ref={wrapRef}
                >
                    <div
                        className="mc-canvas-fit__inner"
                        ref={innerRef}
                        style={{
                            width: natural.width,
                            /* No explicit height — measuredH picks up
                               the real rendered bottom so fit-to-view
                               centers on the true bounding box. */
                            transform: `translate(${pan?.x || 0}px, ${pan?.y || 0}px) scale(${effective})`,
                            transformOrigin: "0 0",
                        }}
                    >
                        <OrchCanvas
                            team={team}
                            showLegend={false}
                            canvasWidth={natural.width}
                        />
                    </div>
                </div>
                {/* No bottom legend block — edge labels on the lines
                    inside the canvas already explain the relationships
                    without forcing the user's eyes down-and-back. */}
            </div>
        </div>
    );
}
