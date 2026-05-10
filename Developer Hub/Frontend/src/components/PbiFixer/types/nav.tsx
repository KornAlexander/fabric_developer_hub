// Flat-tree navigation for the PBI Fixer shell.
// Owned by WS-A. Other workstreams reference NavKey / NAV_ITEMS to plug
// their pages into the shell without touching the shell code itself.
//
// WS-O Phase 1 (v1.2): icon glyphs are now FluentUI 20 px React nodes
// (was emoji strings) so the PBI Fixer chrome reads as a first-class
// citizen of the Developer Hub. Each icon below has been verified to
// exist in the bundled `@fluentui/react-icons` package — see the
// WS-J v0.20 gotcha for why this matters.

import React from "react";
import {
  Database20Regular,
  ChartMultiple20Regular,
  Wrench20Regular,
  DatabaseSearch20Regular,
  DocumentSearch20Regular,
  Storage20Regular,
  Eye20Regular,
  Translate20Regular,
  ArrowSwap20Regular,
  Flowchart20Regular,
  Beaker20Regular,
  ArrowImport20Regular,
  Flash20Regular,
} from "@fluentui/react-icons";

export type NavKey =
  | "model"
  | "report"
  | "fixer"
  | "modelBpa"
  | "reportBpa"
  | "memory"
  | "perspectives"
  | "translations"
  | "delta"
  | "diagram"
  | "prototype"
  | "reversePrototype"
  | "sempyRunner";

/** Sub-group identifiers used to partition the nav into themed,
 *  collapsible sections. v0.34 replaces the single catch-all "Others"
 *  branch with three themed groups so the rail collapses cleanly. */
export type NavGroup = "peer" | "modelTools" | "reportTools" | "automation";

export interface NavItem {
  key: NavKey;
  label: string;
  /** FluentUI 20 px regular icon. Rendered as a `React.ReactNode` so the
   *  consumer can place it inside any markup without further wrapping. */
  icon: React.ReactNode;
  /** "peer" items render flat at the top (or bottom) of the nav.
   *  Anything else falls under that group's collapsible header. */
  group: NavGroup;
  /** Whether the page is production-ready (false → shows "Coming soon"). */
  ready: boolean;
}

// v0.34: Model/Report stay as flat peers at the top; About becomes a
// flat peer at the bottom (it's meta — shouldn't hide inside a group).
// The 13 former "Others" entries now split into three themed groups:
//   • Model tools  — analyse / maintain the semantic model
//   • Report tools — design / inspect reports
//   • Automation   — execute code (TMSL / C# / Python) against items
//
// WS-O acceptance #10: nav items with `ready: false` MUST NOT render in
// the sidebar (matches AgentHub — never advertise unfinished pages).
// We keep the unready entries in `ALL_NAV_ITEMS` so the NavKey union +
// any deep-link / preselect logic that mentions them still resolves; the
// public `NAV_ITEMS` export (consumed by `AgentHubLayout` to render the
// rail) is the filtered subset. CHANGELOG / PLAN remain the roadmap.
const ALL_NAV_ITEMS: NavItem[] = [
  // Top peers
  { key: "model",            label: "Model",             icon: <Database20Regular />,        group: "peer",         ready: true  },
  { key: "report",           label: "Report",            icon: <ChartMultiple20Regular />,   group: "peer",         ready: true  },

  // Model tools — modelBpa / memory consolidated into Model Explorer (v0.102)
  { key: "modelBpa",         label: "Model BPA",         icon: <DatabaseSearch20Regular />,  group: "modelTools",   ready: false },
  { key: "memory",           label: "Memory Analyzer",   icon: <Storage20Regular />,         group: "modelTools",   ready: false },
  { key: "perspectives",     label: "Perspectives",      icon: <Eye20Regular />,             group: "modelTools",   ready: true  },
  { key: "translations",     label: "Translations",      icon: <Translate20Regular />,       group: "modelTools",   ready: true  },
  { key: "delta",            label: "Delta",             icon: <ArrowSwap20Regular />,       group: "modelTools",   ready: true  },
  { key: "diagram",          label: "Diagram",           icon: <Flowchart20Regular />,       group: "modelTools",   ready: true  },

  // Report tools — reportBpa consolidated into Report Explorer (v0.102)
  { key: "reportBpa",        label: "Report BPA",        icon: <DocumentSearch20Regular />,  group: "reportTools",  ready: false },
  { key: "prototype",        label: "Prototype",         icon: <Beaker20Regular />,          group: "reportTools",  ready: true  },
  { key: "reversePrototype", label: "Reverse Prototype", icon: <ArrowImport20Regular />,     group: "reportTools",  ready: true  },

  // Automation
  { key: "fixer",            label: "Fixer",             icon: <Wrench20Regular />,          group: "automation",   ready: true  },
  { key: "sempyRunner",      label: "Sempy Runner",      icon: <Flash20Regular />,           group: "automation",   ready: true  },
];

/** All nav entries including those gated behind `ready: false`. Use only
 *  for type-level enumeration; never render this directly. */
export const ALL_NAV_ITEMS_REGISTRY: NavItem[] = ALL_NAV_ITEMS;

/** Public nav list rendered by the sidebar. Filters out entries that are
 *  not yet production-ready so the rail never advertises unfinished
 *  pages (WS-O acceptance #10). */
export const NAV_ITEMS: NavItem[] = ALL_NAV_ITEMS.filter((i) => i.ready);

/** Ordered list of collapsible groups. Used by the sidebar renderer
 *  to draw a header row + (when expanded) the group's items. */
export interface NavGroupDef {
  key: Exclude<NavGroup, "peer">;
  label: string;
}

export const NAV_GROUPS: NavGroupDef[] = [
  { key: "modelTools",  label: "Model tools"  },
  { key: "reportTools", label: "Report tools" },
  { key: "automation",  label: "Automation"   },
];

export const DEFAULT_NAV_KEY: NavKey = "model";
