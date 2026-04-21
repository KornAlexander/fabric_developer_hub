/* ═══════════════════════════════════════════════════════════════════
   03-mission-control — team-panel controller.
   Handles collapse/expand, layout cycling, and overflow detection
   on every .mc-team-panel instance.
   ═══════════════════════════════════════════════════════════════════ */
(function () {
    "use strict";

    const LAYOUTS = ["supervisor", "sequential", "network", "solo"];
    const LABELS  = {
        supervisor: "Supervisor",
        sequential: "Sequential",
        network:    "Network",
        solo:       "Solo"
    };

    function setLayout(panel, layout) {
        panel.dataset.topology = layout;
        panel.querySelectorAll('.mc-team-strip, .mc-team-panel__full').forEach(el => {
            el.dataset.topology = layout;
        });
        const label = panel.querySelector('.mc-team-panel__layout-label');
        if (label) label.textContent = LABELS[layout] || layout;
        // Recheck overflow after layout change (widths differ)
        requestAnimationFrame(() => measureOverflow(panel));
    }

    function cycleLayout(panel) {
        const current = panel.dataset.topology || "supervisor";
        const idx = LAYOUTS.indexOf(current);
        const next = LAYOUTS[(idx + 1) % LAYOUTS.length];
        setLayout(panel, next);
    }

    function setExpanded(panel, expanded) {
        panel.dataset.expanded = expanded ? "true" : "false";
        const btn  = panel.querySelector('[data-action="toggle-expand"]');
        const icon = panel.querySelector('[data-role="expand-icon"]');
        const lbl  = panel.querySelector('[data-role="expand-label"]');
        if (btn) {
            btn.setAttribute("aria-pressed", expanded ? "true" : "false");
            btn.setAttribute("aria-expanded", expanded ? "true" : "false");
            btn.title = expanded ? "Collapse team view" : "Show full team view";
        }
        if (icon) icon.textContent = expanded ? "expand_less" : "expand_more";
        if (lbl)  lbl.textContent  = expanded ? "Collapse" : "Expand";
        if (!expanded) requestAnimationFrame(() => measureOverflow(panel));
    }

    /* ─── Overflow detection ──────────────────────────────────────────
       Measures the workers row. If total chip width exceeds available
       width, hides trailing chips and injects a "+N more" chip.
       Re-runs on resize and after layout changes. */
    function measureOverflow(panel) {
        if (panel.dataset.expanded === "true") return; // not relevant in expanded view
        const strip = panel.querySelector('.mc-team-strip');
        if (!strip) return;
        const workers = strip.querySelector('.mc-team-strip__workers');
        if (!workers) return;

        // Reset any previous overflow state
        workers.querySelectorAll('[data-overflow-hidden]').forEach(el => {
            el.removeAttribute('data-overflow-hidden');
            el.style.display = '';
        });
        const oldTag = workers.querySelector('.mc-agent-chip--overflow');
        if (oldTag) oldTag.remove();

        // Measure natural width vs available
        const chips = Array.from(workers.children).filter(
            el => el.classList.contains('mc-agent-chip') && !el.classList.contains('mc-agent-chip--overflow')
        );
        if (chips.length === 0) return;

        // Force layout, then check scroll overflow
        const available = workers.clientWidth;
        // Build running total of chip widths + gaps
        const gap = parseFloat(getComputedStyle(workers).gap) || 12;
        let used = 0;
        let visible = 0;
        for (let i = 0; i < chips.length; i++) {
            const w = chips[i].offsetWidth;
            const addGap = i > 0 ? gap : 0;
            if (used + addGap + w <= available) {
                used += addGap + w;
                visible++;
            } else {
                break;
            }
        }

        // Always keep at least 1 visible
        if (visible < 1) visible = 1;

        // If all fit, no overflow chip needed
        if (visible >= chips.length) return;

        // Reserve room for the overflow chip itself (~72px)
        const overflowWidth = 72;
        if (used + gap + overflowWidth > available && visible > 1) {
            visible--; // drop one more to make room
        }

        // Hide the overflowing chips
        const hiddenCount = chips.length - visible;
        for (let i = visible; i < chips.length; i++) {
            chips[i].setAttribute('data-overflow-hidden', 'true');
            chips[i].style.display = 'none';
        }

        // Inject the "+N more" indicator
        const tag = document.createElement('button');
        tag.type = 'button';
        tag.className = 'mc-agent-chip mc-agent-chip--overflow';
        tag.setAttribute('data-state', 'planned');
        tag.setAttribute('aria-label', hiddenCount + ' more agents not shown — expand to view');
        tag.title = 'Expand to view all ' + (chips.length + 1) + ' agents';
        tag.textContent = '+' + hiddenCount;
        tag.addEventListener('click', () => setExpanded(panel, true));
        workers.appendChild(tag);
    }

    /* ─── Wire up each panel ─────────────────────────────────────────── */
    function initPanel(panel) {
        // Keep strip + full view topology in sync on init
        setLayout(panel, panel.dataset.topology || "supervisor");

        panel.addEventListener('click', (ev) => {
            const btn = ev.target.closest('[data-action]');
            if (!btn || !panel.contains(btn)) return;
            const action = btn.getAttribute('data-action');
            if (action === 'cycle-layout')  cycleLayout(panel);
            if (action === 'toggle-expand') setExpanded(panel, panel.dataset.expanded !== "true");
        });

        // Initial + debounced resize overflow check
        requestAnimationFrame(() => measureOverflow(panel));
    }

    // Debounce resize handler across all panels
    let resizeTimer;
    window.addEventListener('resize', () => {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(() => {
            document.querySelectorAll('.mc-team-panel').forEach(measureOverflow);
        }, 120);
    });

    // Boot
    document.querySelectorAll('.mc-team-panel').forEach(initPanel);

    // Re-measure when a screen becomes active (widths can change due to async layout)
    const obs = new MutationObserver(() => {
        document.querySelectorAll('.mc-team-panel').forEach(measureOverflow);
    });
    document.querySelectorAll('.screen').forEach(s => {
        obs.observe(s, { attributes: true, attributeFilter: ['data-active'] });
    });
})();

/* ═══════════════════════════════════════════════════════════════════
   Run-log agent filter (screen 6).
   ═══════════════════════════════════════════════════════════════════ */
(function () {
    "use strict";
    document.querySelectorAll('.mc-run-log').forEach((log) => {
        const tabs = log.querySelectorAll('.mc-run-log__tab');
        tabs.forEach((tab) => {
            tab.addEventListener('click', () => {
                const filter = tab.getAttribute('data-filter') || 'all';
                log.dataset.logFilter = filter;
                tabs.forEach(t => t.setAttribute('aria-selected', t === tab ? 'true' : 'false'));
                // Update empty-state visibility
                const visible = log.querySelectorAll('.mc-run-log__entry:not([style*="display: none"])');
                // Count entries matching the filter
                const matches = filter === 'all'
                    ? log.querySelectorAll('.mc-run-log__entry').length
                    : log.querySelectorAll('.mc-run-log__entry[data-agent="' + filter + '"]').length;
                log.dataset.empty = matches === 0 ? 'true' : 'false';
            });
        });
    });
})();
