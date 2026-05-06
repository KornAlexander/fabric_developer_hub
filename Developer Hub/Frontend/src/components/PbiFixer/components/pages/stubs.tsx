// Stub pages for all "Others" nav items. Each is a placeholder owned by
// WS-A so later workstreams (WS-B … WS-N) can replace the export in
// one focused PR without touching the shell.
//
// Icons are pulled from `NAV_ITEMS` by key so we have exactly one source
// of truth (and no JSX-attribute escape-sequence surprises).

import React from "react";
import { makeStyles, shorthands, Text, Title3 } from "@fluentui/react-components";
import type { PageProps } from "../../types/shared";
import { NAV_ITEMS, NavKey } from "../../types/nav";

const useStyles = makeStyles({
  empty: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    height: "100%",
    ...shorthands.gap("8px"),
    color: "var(--colorNeutralForeground3)",
    textAlign: "center",
    ...shorthands.padding("48px", "24px"),
  },
  icon: {
    fontSize: "48px",
    lineHeight: "1",
  },
});

function ComingSoon(props: { navKey: NavKey; detail: string }) {
  const styles = useStyles();
  const meta = NAV_ITEMS.find((i) => i.key === props.navKey);
  return (
    <div className={styles.empty}>
      <div className={styles.icon}>{meta?.icon}</div>
      <Title3>{meta?.label}</Title3>
      <Text>Coming soon — {props.detail}</Text>
    </div>
  );
}

// WS-E replaces the Fixer execution stub with the real page — see
// ``FixerPage.tsx``. Re-exported from ``pages/index.ts``.

// WS-C replaces the Model BPA stub with the real page — see
// ``ModelBpaPage.tsx``. Re-exported from ``pages/index.ts``.

// WS-D replaces the Report BPA stub with the real page — see
// ``ReportBpaPage.tsx``. Re-exported from ``pages/index.ts``.

// WS-B replaces the Memory stub with the real page — see
// ``MemoryPage.tsx``. Re-exported from ``pages/index.ts``. The legacy
// `vertipaq` nav key was retired in WS-B (Memory == Vertipaq Analyzer per Python parity).

// WS-F replaces the Perspectives stub with the real page — see
// ``PerspectivesPage.tsx``. Re-exported from ``pages/index.ts``.

// WS-G replaces the Translations stub with the real page — see
// ``TranslationsPage.tsx``. Re-exported from ``pages/index.ts``.

// WS-I replaces the Delta stub with the real page — see
// ``DeltaAnalyzerPage.tsx``. Re-exported from ``pages/index.ts``.

// WS-J replaces the Diagram stub with the real page — see
// ``DiagramPage.tsx``. Re-exported from ``pages/index.ts``.

// WS-M replaces the Prototype stub with the real page — see
// ``PrototypePage.tsx``. Re-exported from ``pages/index.ts``.

// WL-A v1.13: the About page moved out of the PBI Fixer entirely
// (it's a hub-wide concern, not a Fixer concern). The hub-level
// ``AboutPage`` lives at ``Frontend/src/components/AgentHub/AboutPage.tsx``
// and is opened from the sidebar footer above Support.
