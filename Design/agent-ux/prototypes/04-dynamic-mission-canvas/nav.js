/* 04-dynamic-mission-canvas — static offline mission simulation. */
(function () {
    "use strict";

    const logModes = [
        { screen: "2", label: "High-level logs", icon: "view_agenda" },
        { screen: "3", label: "Detailed logs", icon: "subject" },
        { screen: "4", label: "Diagnostic logs", icon: "bug_report" }
    ];

    const diagnosticLogs = {
        generalist: [
            { time: "[08:43:02.412]", kind: "EVENT", text: "Parallel dispatch started for Fabric Data Engineer, Fabric Admin, and Modeler." },
            { time: "[08:43:09.118]", kind: "TOOL", text: "Workspace inventory request queued with approval guard enabled." },
            { time: "[08:43:12.604]", kind: "HEALTH", text: "No retries or timeouts detected in the current parallel wave." }
        ],
        "data-engineer": [
            { time: "[08:43:18.210]", kind: "API", text: "Fabric items list completed for Fabric ClawHub with a successful response." },
            { time: "[08:43:22.441]", kind: "TOOL", text: "Lineage scan read Pipeline_1 and FabricItems_Lakehouse metadata." },
            { time: "[08:43:27.982]", kind: "DIAG", text: "dependency_map.json write staged; no destructive actions requested." }
        ],
        admin: [
            { time: "[08:43:20.334]", kind: "API", text: "Capacity metrics read completed; F2 utilization spike observed." },
            { time: "[08:43:29.017]", kind: "TOOL", text: "Workspace access review found legacy principal ServiceAcc_Old.", warn: true },
            { time: "[08:43:34.526]", kind: "GUARD", text: "Capacity and tenant setting changes require approval before execution." }
        ],
        modeler: [
            { time: "[08:43:21.266]", kind: "API", text: "Semantic model metadata read completed for FinanceModel." },
            { time: "[08:43:25.739]", kind: "TOOL", text: "Certification label check returned missing for the target model." },
            { time: "[08:43:31.455]", kind: "DIAG", text: "Schema comparison completed with nullable drift only." }
        ]
    };

    function replaceDiagnosticLogs(screen) {
        Object.keys(diagnosticLogs).forEach((nodeKey) => {
            const node = screen.querySelector('[data-node="' + nodeKey + '"]');
            const log = node && node.querySelector(".log-window");
            if (!log) return;

            const rows = diagnosticLogs[nodeKey].map((entry) => {
                const row = document.createElement("p");
                if (entry.warn) row.className = "log-warn";

                const time = document.createElement("time");
                time.textContent = entry.time;
                const kind = document.createElement("span");
                kind.className = "log-kind log-kind--" + entry.kind.toLowerCase();
                kind.textContent = entry.kind;

                row.append(time, " ", kind, " ", entry.text);
                return row;
            });
            log.replaceChildren(...rows);
        });
    }

    function shiftScreensForDiagnosticMode() {
        document.querySelectorAll(".screen").forEach((screen) => {
            const screenNumber = parseInt(screen.dataset.screen || "", 10);
            if (screenNumber >= 4) screen.dataset.screen = String(screenNumber + 1);
        });

        document.querySelectorAll("[data-goto]").forEach((target) => {
            const value = parseInt(target.getAttribute("data-goto") || "", 10);
            if (value >= 4) target.setAttribute("data-goto", String(value + 1));
        });
    }

    function ensureDiagnosticScreen() {
        const detailed = document.querySelector('.screen[data-screen="3"]');
        if (!detailed || document.querySelector('.screen[data-log-mode="diagnostic"]')) return;

        shiftScreensForDiagnosticMode();

        const diagnostic = detailed.cloneNode(true);
        diagnostic.dataset.screen = "4";
        diagnostic.dataset.logMode = "diagnostic";
        diagnostic.setAttribute("aria-label", "Diagnostic log view");

        const status = diagnostic.querySelector(".mission-status");
        if (status) status.innerHTML = '<span class="live-dot"></span> Running · diagnostics visible · 4 agents active';

        replaceDiagnosticLogs(diagnostic);
        detailed.after(diagnostic);
    }

    function setupLogModeSwitches() {
        document.querySelectorAll(".log-mode-switch").forEach((switcher) => {
            const screen = switcher.closest(".screen");
            const currentScreen = screen ? screen.dataset.screen : "";
            const existingActive = switcher.querySelector(".log-mode-switch__option.is-active");
            const activeScreen = logModes.some((mode) => mode.screen === currentScreen) ? currentScreen : (existingActive && existingActive.getAttribute("data-goto")) || "3";

            logModes.forEach((mode) => {
                let option = Array.from(switcher.querySelectorAll(".log-mode-switch__option")).find((candidate) => candidate.getAttribute("data-goto") === mode.screen);
                if (!option) {
                    option = document.createElement("button");
                    option.className = "log-mode-switch__option";
                    option.type = "button";
                    switcher.appendChild(option);
                }

                option.setAttribute("data-goto", mode.screen);
                option.setAttribute("aria-pressed", activeScreen === mode.screen ? "true" : "false");
                option.classList.toggle("is-active", activeScreen === mode.screen);
                option.replaceChildren(document.createElement("span"), " " + mode.label);
                option.firstElementChild.className = "material-symbols-outlined";
                option.firstElementChild.textContent = mode.icon;
            });
        });
    }

    ensureDiagnosticScreen();
    setupLogModeSwitches();

    const screens = Array.from(document.querySelectorAll(".screen"));
    const total = screens.length;
    let index = 0;

    const prev = document.getElementById("dmc-prev");
    const next = document.getElementById("dmc-next");
    const crumbs = document.getElementById("dmc-crumbs");
    const counter = document.getElementById("dmc-counter");
    const flowLabel = document.getElementById("dmc-flow-label");
    const flowProgress = document.getElementById("dmc-flow-progress");
    const flowToggle = document.getElementById("dmc-flow-toggle");
    const flowReplay = document.getElementById("dmc-flow-replay");
    const logToggle = document.getElementById("dmc-log-toggle");

    const flowSteps = [
        { index: 1, label: "High-level log view", hold: 4300 },
        { index: 2, label: "Detailed log view", hold: 5200 },
        { index: 3, label: "Diagnostic log view", hold: 5000 },
        { index: 4, label: "Generalist inspects and steers a branch", hold: 5200 },
        { index: 5, label: "Subagents return summaries; approval gate opens", hold: 5600 },
        { index: 6, label: "Approved actions apply; mission completes", hold: 5200 }
    ];
    let flowTimer = 0;
    let canvasLineTimer = 0;
    let playing = false;
    let flowStep = -1;
    let canvasInteraction = null;
    const canvasLayouts = new Map();

    function clamp(value, min, max) {
        return Math.min(Math.max(value, min), Math.max(min, max));
    }

    function getCanvasLayoutKey(canvas) {
        if (canvas.dataset.canvasLayout) return canvas.dataset.canvasLayout;
        if (canvas.classList.contains("agent-canvas--dense")) return "parallel-wave";
        const screen = canvas.closest(".screen");
        return "screen-" + (screen ? screen.dataset.screen : "canvas");
    }

    function getNodeKey(node) {
        return node.dataset.node || node.getAttribute("aria-label") || "agent";
    }

    function getCanvasLayout(canvas) {
        const key = getCanvasLayoutKey(canvas);
        if (!canvasLayouts.has(key)) canvasLayouts.set(key, new Map());
        return canvasLayouts.get(key);
    }

    function formatPercent(value, total) {
        return (value / Math.max(total, 1) * 100).toFixed(2) + "%";
    }

    function minNodeWidth(node) {
        return node.classList.contains("agent-node--generalist") || node.classList.contains("agent-node--compact") ? 300 : 180;
    }

    function applyNodeLayout(node, layout) {
        if (layout.x) node.style.setProperty("--x", layout.x, "important");
        if (layout.y) node.style.setProperty("--y", layout.y, "important");
        if (layout.w) node.style.setProperty("--node-w", layout.w);
        if (layout.logH) {
            node.style.setProperty("--agent-log-h", layout.logH);
            node.setAttribute("data-manual-size", "true");
        }
    }

    function syncNodeLayout(layoutKey, nodeKey, layout) {
        document.querySelectorAll(".agent-canvas").forEach((canvas) => {
            if (getCanvasLayoutKey(canvas) !== layoutKey) return;
            const match = Array.from(canvas.querySelectorAll(".agent-node")).find((node) => getNodeKey(node) === nodeKey);
            if (match) applyNodeLayout(match, layout);
        });
    }

    function setNodeLayout(canvas, node, patch) {
        const layout = getCanvasLayout(canvas);
        const nodeKey = getNodeKey(node);
        const next = Object.assign({}, layout.get(nodeKey) || {}, patch);
        layout.set(nodeKey, next);
        syncNodeLayout(getCanvasLayoutKey(canvas), nodeKey, next);
        updateCanvasLines();
    }

    function resetNodeLayout(node) {
        const initialX = node.dataset.initialX;
        const initialY = node.dataset.initialY;
        const initialW = node.dataset.initialNodeW;
        const initialLogH = node.dataset.initialLogH;

        if (initialX) node.style.setProperty("--x", initialX);
        else node.style.removeProperty("--x");
        if (initialY) node.style.setProperty("--y", initialY);
        else node.style.removeProperty("--y");
        if (initialW) node.style.setProperty("--node-w", initialW);
        else node.style.removeProperty("--node-w");
        if (initialLogH) node.style.setProperty("--agent-log-h", initialLogH);
        else node.style.removeProperty("--agent-log-h");
        node.removeAttribute("data-manual-size");
    }

    function resetCanvasLayout(canvas) {
        const layoutKey = getCanvasLayoutKey(canvas);
        canvasLayouts.delete(layoutKey);
        document.querySelectorAll(".agent-canvas").forEach((targetCanvas) => {
            if (getCanvasLayoutKey(targetCanvas) !== layoutKey) return;
            targetCanvas.scrollLeft = 0;
            targetCanvas.scrollTop = 0;
            targetCanvas.querySelectorAll(".agent-node").forEach(resetNodeLayout);
        });
        collapseExpandedLogs();
        scheduleCanvasLineUpdate();
    }

    function setupCanvasInteractions() {
        document.querySelectorAll(".agent-canvas").forEach((canvas) => {
            canvas.dataset.canvasLayout = getCanvasLayoutKey(canvas);

            const toolbar = canvas.querySelector(".canvas-toolbar");
            if (toolbar && !toolbar.querySelector("[data-canvas-reset]")) {
                const group = document.createElement("div");
                group.className = "canvas-view-controls";
                const reset = document.createElement("button");
                reset.type = "button";
                reset.className = "canvas-view-button";
                reset.dataset.canvasReset = "true";
                reset.title = "Reset canvas view";
                reset.setAttribute("aria-label", "Reset canvas view");
                reset.innerHTML = '<span class="material-symbols-outlined" aria-hidden="true">restart_alt</span>';
                group.appendChild(reset);
                toolbar.appendChild(group);
            }

            canvas.querySelectorAll(".agent-node").forEach((node) => {
                if (!node.dataset.initializedLayout) {
                    node.dataset.initializedLayout = "true";
                    node.dataset.initialX = node.style.getPropertyValue("--x");
                    node.dataset.initialY = node.style.getPropertyValue("--y");
                    node.dataset.initialNodeW = node.style.getPropertyValue("--node-w");
                    node.dataset.initialLogH = node.style.getPropertyValue("--agent-log-h");
                }

                if (!node.querySelector(".agent-card-resize")) {
                    const handle = document.createElement("button");
                    handle.type = "button";
                    handle.className = "agent-card-resize";
                    handle.title = "Resize agent card";
                    handle.setAttribute("aria-label", "Resize agent card");
                    handle.innerHTML = '<span class="material-symbols-outlined" aria-hidden="true">open_in_full</span>';
                    node.appendChild(handle);
                }
            });
        });
    }

    function startCanvasInteraction(event, node, type) {
        const canvas = node.closest(".agent-canvas");
        if (!canvas || getComputedStyle(node).position !== "absolute") return;

        event.preventDefault();
        stopFlow("Manual review mode");
        collapseExpandedLogs();

        const computed = getComputedStyle(node);
        const log = node.querySelector(".log-window");
        const nodeRect = node.getBoundingClientRect();
        const logRect = log ? log.getBoundingClientRect() : { height: 0 };
        const startLeft = parseFloat(computed.left) || 0;
        const startTop = parseFloat(computed.top) || 0;

        canvasInteraction = {
            type,
            canvas,
            node,
            pointerId: event.pointerId,
            startX: event.clientX,
            startY: event.clientY,
            startLeft,
            startTop,
            startWidth: nodeRect.width,
            startLogH: logRect.height,
            nonLogHeight: Math.max(0, nodeRect.height - logRect.height)
        };

        node.classList.toggle("is-dragging", type === "drag");
        node.classList.toggle("is-resizing", type === "resize");
        canvas.classList.add("is-canvas-interacting");
        try {
            if (event.target.setPointerCapture) event.target.setPointerCapture(event.pointerId);
        } catch (_) {
            // Synthetic validation events may not own an active pointer.
        }
    }

    function updateCanvasInteraction(event) {
        if (!canvasInteraction || event.pointerId !== canvasInteraction.pointerId) return;
        event.preventDefault();

        const dx = event.clientX - canvasInteraction.startX;
        const dy = event.clientY - canvasInteraction.startY;
        const canvas = canvasInteraction.canvas;
        const node = canvasInteraction.node;

        if (canvasInteraction.type === "drag") {
            const width = node.getBoundingClientRect().width;
            const height = node.getBoundingClientRect().height;
            const maxLeft = canvas.clientWidth - width - 8;
            const maxTop = canvas.clientHeight - height - 8;
            const left = clamp(canvasInteraction.startLeft + dx, 8, maxLeft);
            const top = clamp(canvasInteraction.startTop + dy, 8, maxTop);
            setNodeLayout(canvas, node, {
                x: formatPercent(left, canvas.clientWidth),
                y: formatPercent(top, canvas.clientHeight)
            });
            return;
        }

        const maxWidth = Math.min(660, canvas.clientWidth - canvasInteraction.startLeft - 8);
        const width = clamp(canvasInteraction.startWidth + dx, minNodeWidth(node), maxWidth);
        const availableLogH = canvas.clientHeight - canvasInteraction.startTop - canvasInteraction.nonLogHeight - 8;
        const logH = clamp(canvasInteraction.startLogH + dy, 84, Math.min(420, availableLogH));
        setNodeLayout(canvas, node, {
            w: Math.round(width) + "px",
            logH: Math.round(logH) + "px"
        });
    }

    function endCanvasInteraction(event) {
        if (!canvasInteraction || event.pointerId !== canvasInteraction.pointerId) return;
        canvasInteraction.node.classList.remove("is-dragging", "is-resizing");
        canvasInteraction.canvas.classList.remove("is-canvas-interacting");
        canvasInteraction = null;
        scheduleCanvasLineUpdate();
    }

    function resizeNodeFromKeyboard(event, button) {
        const node = button.closest(".agent-node");
        const canvas = node && node.closest(".agent-canvas");
        if (!node || !canvas || getComputedStyle(node).position !== "absolute") return false;
        const keys = ["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp"];
        if (!keys.includes(event.key)) return false;

        event.preventDefault();
        stopFlow("Manual review mode");
        const step = event.shiftKey ? 40 : 18;
        const computed = getComputedStyle(node);
        const left = parseFloat(computed.left) || 0;
        const top = parseFloat(computed.top) || 0;
        const log = node.querySelector(".log-window");
        const nodeRect = node.getBoundingClientRect();
        const logH = log ? log.getBoundingClientRect().height : 0;
        const nonLogHeight = Math.max(0, nodeRect.height - logH);
        const nextWidth = nodeRect.width + (event.key === "ArrowRight" ? step : event.key === "ArrowLeft" ? -step : 0);
        const nextLogH = logH + (event.key === "ArrowDown" ? step : event.key === "ArrowUp" ? -step : 0);
        const maxWidth = Math.min(660, canvas.clientWidth - left - 8);
        const availableLogH = canvas.clientHeight - top - nonLogHeight - 8;
        setNodeLayout(canvas, node, {
            w: Math.round(clamp(nextWidth, minNodeWidth(node), maxWidth)) + "px",
            logH: Math.round(clamp(nextLogH, 84, Math.min(420, availableLogH))) + "px"
        });
        return true;
    }

    function render() {
        screens.forEach((screen, i) => {
            const active = i === index;
            screen.dataset.active = active ? "true" : "false";
            screen.setAttribute("aria-hidden", active ? "false" : "true");
            if (active) {
                screen.classList.remove("screen-replay");
                void screen.offsetWidth;
                screen.classList.add("screen-replay");
            } else {
                screen.classList.remove("screen-replay");
            }
        });

        if (counter) counter.textContent = (index + 1) + " / " + total;
        if (prev) prev.disabled = index === 0;
        if (next) next.disabled = index === total - 1;

        if (crumbs) {
            Array.from(crumbs.children).forEach((button, i) => {
                button.setAttribute("aria-current", i === index ? "true" : "false");
                if (i < index) button.setAttribute("data-state", "done");
                else button.removeAttribute("data-state");
            });
        }

        scheduleCanvasLineUpdate();
    }

    function goTo(nextIndex) {
        if (nextIndex < 0 || nextIndex >= total) return;
        index = nextIndex;
        if (!playing) updateFlowChrome();
        render();
    }

    function setFlowState(state) {
        document.body.dataset.flowState = state;
        if (!flowToggle) return;
        const icon = flowToggle.querySelector(".material-symbols-outlined");
        const text = state === "playing" ? "Pause" : "Play flow";
        if (icon) icon.textContent = state === "playing" ? "pause" : "play_arrow";
        flowToggle.lastChild.textContent = " " + text;
    }

    function setLogSize(size) {
        document.body.dataset.logSize = size;
        if (!logToggle) return;
        const icon = logToggle.querySelector(".material-symbols-outlined");
        if (icon) icon.textContent = size === "large" ? "close_fullscreen" : "open_in_full";
        logToggle.lastChild.textContent = size === "large" ? " Compact logs" : " Large logs";
        scheduleCanvasLineUpdate();
    }

    function collapseExpandedLogs() {
        document.querySelectorAll(".agent-node.is-log-expanded").forEach((node) => node.classList.remove("is-log-expanded"));
    }

    function pointFor(rect, canvasRect, scrollLeft, scrollTop, edge) {
        return {
            x: rect.left + rect.width / 2 - canvasRect.left + scrollLeft,
            y: (edge === "top" ? rect.top : rect.bottom) - canvasRect.top + scrollTop
        };
    }

    function updateCanvasLines() {
        document.querySelectorAll(".agent-canvas").forEach((canvas) => {
            const svg = canvas.querySelector(".canvas-lines");
            if (!svg) return;

            const canvasRect = canvas.getBoundingClientRect();
            const width = Math.max(canvas.scrollWidth, canvas.clientWidth, 1);
            const height = Math.max(canvas.scrollHeight, canvas.clientHeight, 1);
            svg.setAttribute("viewBox", "0 0 " + width + " " + height);

            svg.querySelectorAll("path[data-from][data-to]").forEach((path) => {
                const from = canvas.querySelector('[data-node="' + path.dataset.from + '"]');
                const to = canvas.querySelector('[data-node="' + path.dataset.to + '"]');
                if (!from || !to) return;

                const fromRect = from.getBoundingClientRect();
                const toRect = to.getBoundingClientRect();
                const travelsUp = fromRect.top > toRect.top;
                const start = pointFor(fromRect, canvasRect, canvas.scrollLeft, canvas.scrollTop, travelsUp ? "top" : "bottom");
                const end = pointFor(toRect, canvasRect, canvas.scrollLeft, canvas.scrollTop, travelsUp ? "bottom" : "top");
                const distanceY = Math.abs(end.y - start.y);
                const bend = Math.max(46, Math.min(150, distanceY * .52));
                const cp1Y = start.y + (travelsUp ? -bend : bend);
                const cp2Y = end.y + (travelsUp ? bend : -bend);

                path.setAttribute("d", "M " + start.x.toFixed(1) + " " + start.y.toFixed(1) + " C " + start.x.toFixed(1) + " " + cp1Y.toFixed(1) + ", " + end.x.toFixed(1) + " " + cp2Y.toFixed(1) + ", " + end.x.toFixed(1) + " " + end.y.toFixed(1));
            });
        });
    }

    function scheduleCanvasLineUpdate() {
        window.clearTimeout(canvasLineTimer);
        window.requestAnimationFrame(() => {
            updateCanvasLines();
            window.requestAnimationFrame(updateCanvasLines);
        });
        canvasLineTimer = window.setTimeout(updateCanvasLines, 80);
    }

    function updateFlowChrome(label) {
        const screenProgress = Math.max(0, index) / Math.max(1, total - 1);
        if (flowProgress) flowProgress.style.width = Math.round(screenProgress * 100) + "%";
        if (flowLabel) flowLabel.textContent = label || (index === 0 ? "Ready to run offline demo" : "Manual review mode");
    }

    function stopFlow(label) {
        window.clearTimeout(flowTimer);
        flowTimer = 0;
        playing = false;
        flowStep = -1;
        setFlowState("idle");
        updateFlowChrome(label);
    }

    function playNextStep() {
        flowStep += 1;
        if (flowStep >= flowSteps.length) {
            stopFlow("Offline demo complete");
            if (flowProgress) flowProgress.style.width = "100%";
            return;
        }

        const step = flowSteps[flowStep];
        index = step.index;
        render();
        if (flowProgress) flowProgress.style.width = Math.round((flowStep + 1) / flowSteps.length * 100) + "%";
        if (flowLabel) flowLabel.textContent = step.label;
        flowTimer = window.setTimeout(playNextStep, step.hold);
    }

    function startFlow() {
        window.clearTimeout(flowTimer);
        playing = true;
        flowStep = -1;
        setFlowState("playing");
        playNextStep();
    }

    if (crumbs) {
        for (let i = 0; i < total; i++) {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "dmc-nav__crumb";
            button.textContent = String(i + 1);
            button.setAttribute("aria-label", "Go to screen " + (i + 1));
            button.addEventListener("click", () => goTo(i));
            crumbs.appendChild(button);
        }
    }

    if (prev) prev.addEventListener("click", () => { stopFlow("Manual review mode"); goTo(index - 1); });
    if (next) next.addEventListener("click", () => { stopFlow("Manual review mode"); goTo(index + 1); });
    if (flowToggle) flowToggle.addEventListener("click", () => playing ? stopFlow("Offline demo paused") : startFlow());
    if (flowReplay) flowReplay.addEventListener("click", () => { index = 0; render(); startFlow(); });
    if (logToggle) logToggle.addEventListener("click", () => setLogSize(document.body.dataset.logSize === "large" ? "normal" : "large"));

    document.addEventListener("pointerdown", (event) => {
        if (event.button !== 0) return;
        const resizeHandle = event.target.closest(".agent-card-resize");
        if (resizeHandle) {
            const node = resizeHandle.closest(".agent-node");
            if (node) startCanvasInteraction(event, node, "resize");
            return;
        }

        const head = event.target.closest(".agent-head");
        if (!head || event.target.closest("button, a, input, textarea, select, [contenteditable='true']")) return;
        const node = head.closest(".agent-node");
        if (node) startCanvasInteraction(event, node, "drag");
    });

    window.addEventListener("pointermove", updateCanvasInteraction);
    window.addEventListener("pointerup", endCanvasInteraction);
    window.addEventListener("pointercancel", endCanvasInteraction);

    document.addEventListener("click", (event) => {
        const demoStart = event.target.closest("[data-demo-start]");
        if (demoStart) {
            event.preventDefault();
            startFlow();
            return;
        }

        const resetButton = event.target.closest("[data-canvas-reset]");
        if (resetButton) {
            event.preventDefault();
            const canvas = resetButton.closest(".agent-canvas");
            if (canvas) resetCanvasLayout(canvas);
            return;
        }

        const promptSummary = event.target.closest(".prompt-recap__summary");
        if (promptSummary) {
            event.preventDefault();
            const recap = promptSummary.closest(".prompt-recap");
            const isOpen = recap && recap.dataset.open === "true";
            if (recap) recap.dataset.open = isOpen ? "false" : "true";
            promptSummary.setAttribute("aria-expanded", isOpen ? "false" : "true");
            scheduleCanvasLineUpdate();
            return;
        }

        const log = event.target.closest(".agent-node .log-window");
        if (log) {
            event.preventDefault();
            const node = log.closest(".agent-node");
            const isExpanded = node && node.classList.contains("is-log-expanded");
            collapseExpandedLogs();
            if (node && !isExpanded) node.classList.add("is-log-expanded");
            scheduleCanvasLineUpdate();
            return;
        }

        const target = event.target.closest("[data-goto]");
        if (!target) return;
        const value = parseInt(target.getAttribute("data-goto"), 10);
        if (!Number.isNaN(value)) {
            event.preventDefault();
            stopFlow("Manual review mode");
            goTo(value - 1);
        }
    });

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            collapseExpandedLogs();
            setLogSize("normal");
            scheduleCanvasLineUpdate();
            return;
        }
        const resizeButton = event.target.closest && event.target.closest(".agent-card-resize");
        if (resizeButton && resizeNodeFromKeyboard(event, resizeButton)) return;
        const tagName = (event.target && event.target.tagName) || "";
        if (tagName === "INPUT" || tagName === "TEXTAREA" || event.target.isContentEditable) return;
        if (event.key === "ArrowRight") { event.preventDefault(); stopFlow("Manual review mode"); goTo(index + 1); }
        if (event.key === "ArrowLeft") { event.preventDefault(); stopFlow("Manual review mode"); goTo(index - 1); }
    });

    setupCanvasInteractions();
    setFlowState("idle");
    setLogSize("normal");
    updateFlowChrome();
    window.addEventListener("resize", scheduleCanvasLineUpdate);
    window.addEventListener("load", scheduleCanvasLineUpdate);
    render();
})();