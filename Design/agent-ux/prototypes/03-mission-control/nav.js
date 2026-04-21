/* ═══════════════════════════════════════════════════════════════════
   03-mission-control — prototype navigation (no runtime, no network).
   ═══════════════════════════════════════════════════════════════════ */
(function () {
    "use strict";

    const screens = Array.from(document.querySelectorAll(".screen"));
    const total = screens.length;
    let index = 0;

    const $prev     = document.getElementById("mc-prev");
    const $next     = document.getElementById("mc-next");
    const $crumbs   = document.getElementById("mc-crumbs");
    const $counter  = document.getElementById("mc-counter");

    function render() {
        screens.forEach((s, i) => {
            const a = i === index;
            s.dataset.active = a ? "true" : "false";
            s.setAttribute("aria-hidden", a ? "false" : "true");
        });
        if ($counter) $counter.textContent = (index + 1) + " / " + total;
        if ($prev)    $prev.disabled = index === 0;
        if ($next)    $next.disabled = index === total - 1;

        if ($crumbs) {
            Array.from($crumbs.children).forEach((btn, i) => {
                btn.setAttribute("aria-current", i === index ? "true" : "false");
                if (i < index) btn.setAttribute("data-state", "done");
                else btn.removeAttribute("data-state");
            });
        }

        window.scrollTo({ top: 0, left: 0, behavior: "auto" });
    }

    function goTo(i) {
        if (i < 0 || i >= total) return;
        index = i;
        render();
    }

    // Build crumbs once.
    if ($crumbs) {
        for (let i = 0; i < total; i++) {
            const btn = document.createElement("button");
            btn.type = "button";
            btn.className = "mc-nav__crumb";
            btn.textContent = String(i + 1);
            btn.setAttribute("aria-label", "Go to screen " + (i + 1));
            btn.addEventListener("click", () => goTo(i));
            $crumbs.appendChild(btn);
        }
    }

    if ($prev) $prev.addEventListener("click", () => goTo(index - 1));
    if ($next) $next.addEventListener("click", () => goTo(index + 1));

    // data-goto="N" on any element jumps to screen N (1-based).
    document.addEventListener("click", (ev) => {
        const el = ev.target.closest("[data-goto]");
        if (!el) return;
        const t = parseInt(el.getAttribute("data-goto"), 10);
        if (!Number.isNaN(t)) { ev.preventDefault(); goTo(t - 1); }
    });

    // Keyboard arrow nav — ignored when focus is in a field.
    document.addEventListener("keydown", (ev) => {
        const tag = (ev.target && ev.target.tagName) || "";
        if (tag === "INPUT" || tag === "TEXTAREA" || ev.target.isContentEditable) return;
        if (ev.key === "ArrowRight") { ev.preventDefault(); goTo(index + 1); }
        if (ev.key === "ArrowLeft")  { ev.preventDefault(); goTo(index - 1); }
    });

    render();
})();
