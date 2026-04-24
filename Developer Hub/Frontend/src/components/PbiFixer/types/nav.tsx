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
  Code20Regular,
  Beaker20Regular,
  ArrowImport20Regular,
  Flash20Regular,
  Info20Regular,
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
  | "scriptRunner"
  | "prototype"
  | "reversePrototype"
  | "sempyRunner"
  | "about";

export interface NavItem {
  key: NavKey;
  label: string;
  /** FluentUI 20 px regular icon. Rendered as a `React.ReactNode` so the
   *  consumer can place it inside any markup without further wrapping. */
  icon: React.ReactNode;
  /** Peer-level items render at the top of the nav; "others" items are
   *  grouped under a collapsible "Others" branch. */
  group: "peer" | "others";
  /** Whether the page is production-ready (false → shows "Coming soon"). */
  ready: boolean;
}

export const NAV_ITEMS: NavItem[] = [
  { key: "model",        label: "Model",         icon: <Database20Regular />,        group: "peer",   ready: true  },
  { key: "report",       label: "Report",        icon: <ChartMultiple20Regular />,   group: "peer",   ready: true  },
  { key: "fixer",        label: "Fixer",         icon: <Wrench20Regular />,          group: "others", ready: true  },
  { key: "modelBpa",     label: "Model BPA",     icon: <DatabaseSearch20Regular />,  group: "others", ready: true  },
  { key: "reportBpa",    label: "Report BPA",    icon: <DocumentSearch20Regular />,  group: "others", ready: true  },
  { key: "memory",       label: "Memory",        icon: <Storage20Regular />,         group: "others", ready: true  },
  { key: "perspectives", label: "Perspectives",  icon: <Eye20Regular />,             group: "others", ready: true  },
  { key: "translations", label: "Translations",  icon: <Translate20Regular />,       group: "others", ready: true  },
  { key: "delta",        label: "Delta",         icon: <ArrowSwap20Regular />,       group: "others", ready: true  },
  { key: "diagram",      label: "Diagram",       icon: <Flowchart20Regular />,       group: "others", ready: true  },
  { key: "scriptRunner", label: "Script Runner", icon: <Code20Regular />,            group: "others", ready: false },
  { key: "prototype",    label: "Prototype",     icon: <Beaker20Regular />,          group: "others", ready: true  },
  { key: "reversePrototype", label: "Reverse Prototype", icon: <ArrowImport20Regular />, group: "others", ready: true  },
  { key: "sempyRunner", label: "Sempy Runner",  icon: <Flash20Regular />,            group: "others", ready: true  },
  { key: "about",        label: "About",         icon: <Info20Regular />,            group: "others", ready: false },
];

export const DEFAULT_NAV_KEY: NavKey = "model";
