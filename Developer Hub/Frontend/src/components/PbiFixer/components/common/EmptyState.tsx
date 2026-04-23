// Shared empty / loading state for PBI Fixer pages — WS-O Phase 1.9.
// Mirrors the AgentHub home empty-state pattern: small icon + headline +
// optional sub-text + optional CTA. Replaces the bare `Spinner` + grey
// text block that used to appear on every PBI Fixer page.

import React from "react";
import {
  Spinner,
  Text,
  makeStyles,
  shorthands,
  tokens,
} from "@fluentui/react-components";

const useStyles = makeStyles({
  root: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    height: "100%",
    minHeight: "240px",
    color: tokens.colorNeutralForeground2,
    textAlign: "center",
    ...shorthands.gap("12px"),
    ...shorthands.padding("48px", "24px"),
  },
  icon: {
    color: tokens.colorNeutralForeground3,
    fontSize: "32px",
    width: "48px",
    height: "48px",
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
  },
  headline: {
    fontSize: tokens.fontSizeBase400,
    fontWeight: tokens.fontWeightSemibold,
    color: tokens.colorNeutralForeground1,
  },
  sub: {
    fontSize: tokens.fontSizeBase200,
    color: tokens.colorNeutralForeground3,
    maxWidth: "420px",
  },
  cta: {
    marginTop: "8px",
  },
});

export interface EmptyStateProps {
  /** Optional decorative icon — typically a 32 px FluentUI icon. */
  icon?: React.ReactNode;
  /** Bold one-line headline. */
  headline: string;
  /** Optional sub-text. */
  sub?: string;
  /** Optional CTA (button or link). */
  cta?: React.ReactNode;
  /** When true, renders a centered Spinner above the headline. */
  loading?: boolean;
}

export const EmptyState: React.FC<EmptyStateProps> = ({
  icon,
  headline,
  sub,
  cta,
  loading,
}) => {
  const styles = useStyles();
  return (
    <div className={styles.root} role="status">
      {loading ? <Spinner size="small" /> : icon ? <span className={styles.icon} aria-hidden>{icon}</span> : null}
      <Text className={styles.headline}>{headline}</Text>
      {sub && <Text className={styles.sub}>{sub}</Text>}
      {cta && <div className={styles.cta}>{cta}</div>}
    </div>
  );
};
