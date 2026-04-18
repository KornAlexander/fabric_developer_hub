/* ═══════════════════════════════════════════════════════════════════
   Shared AgentHub sidebar (expanded + collapsed icon-rail variant).

   Usage in a page:
     <aside id="agenthub-sidebar"></aside>
     <script src="_shared/sidebar.js"></script>
     <script>renderAgentHubSidebar('new-session');           // expanded</script>
     <script>renderAgentHubSidebar('new-session', { collapsed: true });</script>

   The user's last toggle is persisted in localStorage('agenthub.sidebar.collapsed').
   Pages may pass `{ collapsed: true }` to default to collapsed (overridden by
   any persisted user preference once they toggle).

   activeKey ∈ 'new-session' | 'sessions' | 'agents' | 'pbi-fixer' | null
   ═══════════════════════════════════════════════════════════════════ */
(function () {
  const STORAGE_KEY = 'agenthub.sidebar.collapsed';

  const NAV = {
    'Agent Hub': [
      { key: 'new-session', label: 'New Session',       icon: 'add_circle', activeIcon: true },
      { key: 'sessions',    label: 'Sessions',          icon: 'forum'                         },
      { key: 'agents',      label: 'Agents and Skills', icon: 'smart_toy', activeIcon: true   },
    ],
    'Tools': [
      { key: 'pbi-fixer',   label: 'Power BI Fixer',    icon: 'build'                         },
      { key: null,          label: '\u2026',            icon: 'more_horiz', muted: true       },
      { key: null,          label: '\u2026',            icon: 'more_horiz', muted: true       },
    ],
  };

  // ─── Expanded variant ──────────────────────────────────────────────
  function expandedItem(item, active) {
    if (active) {
      const iconStyle = item.activeIcon ? ' filled" style="color: #005faa;' : '';
      return (
        '<a class="relative flex items-center bg-surface-container-lowest text-on-surface py-2.5 px-3 rounded-lg shadow-sm font-medium text-sm" href="#">' +
          '<div class="absolute left-0 top-1/4 bottom-1/4 w-[3px] bg-primary rounded-full"></div>' +
          '<span class="material-symbols-outlined mr-2.5 text-[20px]' + iconStyle + '">' + item.icon + '</span>' +
          item.label +
        '</a>'
      );
    }
    const textTone = item.muted ? 'text-on-surface-variant/60' : 'text-on-surface-variant';
    return (
      '<a class="flex items-center ' + textTone + ' py-2.5 px-3 hover:bg-surface-container-high/50 rounded-lg transition-all text-sm" href="#">' +
        '<span class="material-symbols-outlined mr-2.5 text-[20px]">' + item.icon + '</span>' +
        item.label +
      '</a>'
    );
  }

  function expandedSection(title, items, activeKey) {
    return (
      '<div>' +
        '<div class="px-3 mb-1.5 text-[10px] font-bold text-on-surface-variant/70 uppercase tracking-widest">' + title + '</div>' +
        '<div class="space-y-0.5">' +
          items.map(i => expandedItem(i, i.key && i.key === activeKey)).join('') +
        '</div>' +
      '</div>'
    );
  }

  function renderExpanded(host, activeKey) {
    host.className = 'w-56 bg-surface-container-low flex flex-col py-5 shrink-0';
    host.innerHTML =
      '<div class="px-5 mb-8 flex items-center justify-between">' +
        '<div class="flex items-center gap-2.5">' +
          '<div class="w-8 h-8 bg-primary-container rounded-lg flex items-center justify-center text-white">' +
            '<span class="material-symbols-outlined text-[16px] filled">dataset</span>' +
          '</div>' +
          '<div>' +
            '<div class="text-sm font-bold text-on-surface leading-tight">AgentHub</div>' +
            '<div class="text-[9px] text-on-surface-variant uppercase tracking-widest font-bold">Fabric Enterprise</div>' +
          '</div>' +
        '</div>' +
        '<button data-agenthub-toggle class="p-1 text-on-surface-variant hover:bg-surface-container-high/50 rounded transition-colors" title="Collapse sidebar">' +
          '<span class="material-symbols-outlined text-[18px]">chevron_left</span>' +
        '</button>' +
      '</div>' +
      '<nav class="flex-1 px-2.5 space-y-4 overflow-y-auto smooth-scroll">' +
        Object.keys(NAV).map(t => expandedSection(t, NAV[t], activeKey)).join('') +
      '</nav>' +
      '<div class="px-2.5 pt-4 border-t border-outline-variant/10 space-y-0.5">' +
        '<a class="flex items-center text-on-surface-variant py-2 px-3 hover:bg-surface-container-high/50 rounded-lg transition-all text-xs" href="#">' +
          '<span class="material-symbols-outlined mr-2 text-[18px]">contact_support</span>Support</a>' +
        '<a class="flex items-center text-on-surface-variant py-2 px-3 hover:bg-surface-container-high/50 rounded-lg transition-all text-xs" href="#">' +
          '<span class="material-symbols-outlined mr-2 text-[18px]">chat_bubble</span>Feedback</a>' +
      '</div>';
  }

  // ─── Collapsed variant (icon rail) ────────────────────────────────
  function collapsedItem(item, active) {
    if (!item.key && item.muted) return ''; // hide the "..." placeholders in collapsed mode
    if (active) {
      const iconExtra = item.activeIcon ? ' filled' : '';
      return (
        '<button class="p-2 bg-surface-container-lowest text-primary rounded-lg shadow-sm relative" title="' + item.label + '">' +
          '<span class="material-symbols-outlined text-[20px]' + iconExtra + '">' + item.icon + '</span>' +
          '<div class="absolute left-0 top-1/4 bottom-1/4 w-[3px] bg-primary rounded-full"></div>' +
        '</button>'
      );
    }
    return (
      '<button class="p-2 text-on-surface-variant hover:bg-surface-container-high rounded-lg transition-all" title="' + item.label + '">' +
        '<span class="material-symbols-outlined text-[20px]">' + item.icon + '</span>' +
      '</button>'
    );
  }

  function collapsedSection(items, activeKey) {
    return items.map(i => collapsedItem(i, i.key && i.key === activeKey)).filter(Boolean).join('');
  }

  function renderCollapsed(host, activeKey) {
    host.className = 'w-12 bg-surface-container-low flex flex-col items-center py-4 gap-3 shrink-0 border-r border-outline-variant/5';
    const sections = Object.keys(NAV);
    host.innerHTML =
      '<div class="w-8 h-8 bg-primary-container rounded-lg flex items-center justify-center text-white mb-1">' +
        '<span class="material-symbols-outlined text-[18px] filled">dataset</span>' +
      '</div>' +
      '<button data-agenthub-toggle class="p-1.5 text-on-surface-variant hover:bg-surface-container-high rounded transition-colors mb-2" title="Expand sidebar">' +
        '<span class="material-symbols-outlined text-[16px]">chevron_right</span>' +
      '</button>' +
      sections.map((t, idx) => {
        const html = collapsedSection(NAV[t], activeKey);
        if (!html) return '';
        const divider = idx > 0 ? '<div class="w-6 h-px bg-outline-variant/20 my-1"></div>' : '';
        return divider + html;
      }).join('') +
      '<div class="mt-auto flex flex-col gap-1 pt-3 border-t border-outline-variant/10 w-8 items-center">' +
        '<button class="p-2 text-on-surface-variant hover:bg-surface-container-high rounded-lg transition-all" title="Support">' +
          '<span class="material-symbols-outlined text-[18px]">contact_support</span>' +
        '</button>' +
        '<button class="p-2 text-on-surface-variant hover:bg-surface-container-high rounded-lg transition-all" title="Feedback">' +
          '<span class="material-symbols-outlined text-[18px]">chat_bubble</span>' +
        '</button>' +
      '</div>';
  }

  // ─── Public renderer ──────────────────────────────────────────────
  window.renderAgentHubSidebar = function (activeKey, opts) {
    opts = opts || {};
    const host = document.getElementById('agenthub-sidebar');
    if (!host) return;

    let stored = null;
    try { stored = localStorage.getItem(STORAGE_KEY); } catch (_) {}
    const collapsed = stored === null ? !!opts.collapsed : stored === '1';

    function paint(c) {
      if (c) renderCollapsed(host, activeKey);
      else   renderExpanded(host, activeKey);
      const toggle = host.querySelector('[data-agenthub-toggle]');
      if (toggle) toggle.addEventListener('click', () => {
        const next = !c;
        try { localStorage.setItem(STORAGE_KEY, next ? '1' : '0'); } catch (_) {}
        paint(next);
      });
    }
    paint(collapsed);
  };
})();
