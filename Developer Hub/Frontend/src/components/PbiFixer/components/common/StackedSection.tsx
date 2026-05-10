// StackedSection — collapsible per-model wrapper used in Multi mode.
//
// In Single mode, a tab renders a single model's view directly. In Multi mode,
// the tab loops over its committed dataset / report list and wraps each
// iteration in a <StackedSection title={name} defaultExpanded={i === 0}>.
//
// The component is intentionally tiny and presentational so any tab can adopt
// it without further refactoring. Per WS-R UX decision: first section starts
// expanded, the rest collapsed.

import * as React from "react";
import {
  ChevronDown20Regular,
  ChevronRight20Regular,
} from "@fluentui/react-icons";
import {
  makeStyles,
  mergeClasses,
  shorthands,
  tokens,
} from "@fluentui/react-components";

const useStyles = makeStyles({
  root: {
    ...shorthands.border("1px", "solid", tokens.colorNeutralStroke2),
    ...shorthands.borderRadius(tokens.borderRadiusMedium),
    backgroundColor: tokens.colorNeutralBackground1,
    marginBottom: "8px",
  },
  header: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    ...shorthands.padding("8px", "12px"),
    cursor: "pointer",
    backgroundColor: tokens.colorNeutralBackground2,
    ...shorthands.borderRadius(
      tokens.borderRadiusMedium,
      tokens.borderRadiusMedium,
      0,
      0,
    ),
    userSelect: "none",
    "&:hover": {
      backgroundColor: tokens.colorNeutralBackground2Hover,
    },
  },
  headerCollapsed: {
    ...shorthands.borderRadius(tokens.borderRadiusMedium),
  },
  titleGroup: {
    display: "flex",
    alignItems: "center",
    columnGap: "6px",
    fontWeight: tokens.fontWeightSemibold,
  },
  meta: {
    color: tokens.colorNeutralForeground3,
    fontSize: tokens.fontSizeBase200,
    fontWeight: tokens.fontWeightRegular,
  },
  body: {
    ...shorthands.padding("12px"),
  },
});

export interface StackedSectionProps {
  title: string;
  defaultExpanded?: boolean;
  expanded?: boolean;
  onToggle?: (next: boolean) => void;
  meta?: React.ReactNode;
  trailing?: React.ReactNode;
  children?: React.ReactNode;
  testId?: string;
}

export const StackedSection: React.FC<StackedSectionProps> = ({
  title,
  defaultExpanded = false,
  expanded,
  onToggle,
  meta,
  trailing,
  children,
  testId,
}) => {
  const styles = useStyles();
  const isControlled = typeof expanded === "boolean";
  const [internalExpanded, setInternalExpanded] = React.useState<boolean>(
    defaultExpanded,
  );
  const isOpen = isControlled ? (expanded as boolean) : internalExpanded;

  const toggle = React.useCallback(() => {
    const next = !isOpen;
    if (!isControlled) setInternalExpanded(next);
    onToggle?.(next);
  }, [isOpen, isControlled, onToggle]);

  return (
    <div className={styles.root} data-testid={testId ?? "stacked-section"}>
      <div
        className={mergeClasses(styles.header, !isOpen && styles.headerCollapsed)}
        role="button"
        tabIndex={0}
        aria-expanded={isOpen}
        onClick={toggle}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            toggle();
          }
        }}
      >
        <span className={styles.titleGroup}>
          {isOpen ? <ChevronDown20Regular /> : <ChevronRight20Regular />}
          <span>{title}</span>
          {meta && <span className={styles.meta}>{meta}</span>}
        </span>
        {trailing && (
          <span
            // Don't toggle when interacting with trailing controls.
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => e.stopPropagation()}
          >
            {trailing}
          </span>
        )}
      </div>
      {isOpen && <div className={styles.body}>{children}</div>}
    </div>
  );
};

export default StackedSection;
