// Left-side flat-tree navigation for the PBI Fixer shell.
// Peer items (Model, Report) render flat. "Others" is a collapsible
// branch whose children visually sit at the same indent as the peers —
// i.e. a flat hierarchy — per the WS-A spec.

import React from "react";
import {
  makeStyles,
  shorthands,
  mergeClasses,
  tokens,
  Text,
} from "@fluentui/react-components";
import { ChevronRight16Regular, ChevronDown16Regular } from "@fluentui/react-icons";
import { NAV_ITEMS, NavKey } from "../types/nav";

const useStyles = makeStyles({
  root: {
    width: "220px",
    minWidth: "220px",
    display: "flex",
    flexDirection: "column",
    ...shorthands.padding("8px", "4px"),
    ...shorthands.gap("2px"),
    ...shorthands.borderRight("1px", "solid", tokens.colorNeutralStroke2),
    backgroundColor: tokens.colorNeutralBackground2,
    height: "100%",
    overflowY: "auto",
  },
  header: {
    fontSize: "11px",
    fontWeight: tokens.fontWeightSemibold,
    color: tokens.colorNeutralForeground3,
    textTransform: "uppercase",
    letterSpacing: "0.5px",
    ...shorthands.padding("4px", "10px", "2px"),
  },
  row: {
    display: "flex",
    alignItems: "center",
    ...shorthands.gap("8px"),
    ...shorthands.padding("6px", "10px"),
    ...shorthands.borderRadius(tokens.borderRadiusMedium),
    cursor: "pointer",
    fontSize: tokens.fontSizeBase300,
    color: tokens.colorNeutralForeground1,
    userSelect: "none",
    ":hover": {
      backgroundColor: tokens.colorNeutralBackground3Hover,
    },
    ":focus-visible": {
      outlineStyle: "solid",
      outlineWidth: "2px",
      outlineColor: tokens.colorStrokeFocus2,
      outlineOffset: "-2px",
    },
  },
  rowActive: {
    backgroundColor: tokens.colorNeutralBackground1Selected,
    fontWeight: tokens.fontWeightSemibold,
  },
  rowDisabled: {
    color: tokens.colorNeutralForeground3,
    fontStyle: "italic",
  },
  icon: {
    width: "20px",
    height: "20px",
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    flex: "0 0 20px",
    color: tokens.colorNeutralForeground2,
  },
  chevron: {
    display: "flex",
    alignItems: "center",
    color: tokens.colorNeutralForeground3,
  },
  othersHeader: {
    // The "Others" row itself acts as the expand toggle — it's never
    // selectable as a page.
    fontWeight: tokens.fontWeightSemibold,
  },
  label: {
    flex: 1,
    whiteSpace: "nowrap",
    overflow: "hidden",
    textOverflow: "ellipsis",
  },
  hint: {
    fontSize: "10px",
    color: tokens.colorNeutralForeground4,
  },
});

export interface PbiFixerNavProps {
  activeKey: NavKey;
  onChange: (key: NavKey) => void;
  othersExpanded: boolean;
  onToggleOthers: () => void;
}

export const PbiFixerNav: React.FC<PbiFixerNavProps> = ({
  activeKey,
  onChange,
  othersExpanded,
  onToggleOthers,
}) => {
  const styles = useStyles();
  const peers = NAV_ITEMS.filter((i) => i.group === "peer");
  const others = NAV_ITEMS.filter((i) => i.group === "others");

  const handleKey = (
    e: React.KeyboardEvent<HTMLDivElement>,
    onClick: () => void,
  ) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onClick();
    }
  };

  return (
    <nav className={styles.root} aria-label="PBI Fixer navigation">
      <div className={styles.header}>PBI Fixer</div>

      {peers.map((item) => (
        <div
          key={item.key}
          role="button"
          tabIndex={0}
          aria-current={activeKey === item.key ? "page" : undefined}
          className={mergeClasses(
            styles.row,
            activeKey === item.key && styles.rowActive,
          )}
          onClick={() => onChange(item.key)}
          onKeyDown={(e) => handleKey(e, () => onChange(item.key))}
        >
          <span className={styles.icon} aria-hidden>
            {item.icon}
          </span>
          <span className={styles.label}>{item.label}</span>
        </div>
      ))}

      <div
        role="button"
        tabIndex={0}
        aria-expanded={othersExpanded}
        className={mergeClasses(styles.row, styles.othersHeader)}
        onClick={onToggleOthers}
        onKeyDown={(e) => handleKey(e, onToggleOthers)}
      >
        <span className={styles.chevron} aria-hidden>
          {othersExpanded ? (
            <ChevronDown16Regular />
          ) : (
            <ChevronRight16Regular />
          )}
        </span>
        <span className={styles.label}>Others</span>
        <Text className={styles.hint}>{others.length}</Text>
      </div>

      {othersExpanded &&
        others.map((item) => (
          <div
            key={item.key}
            role="button"
            tabIndex={0}
            aria-current={activeKey === item.key ? "page" : undefined}
            title={item.ready ? item.label : `${item.label} — Coming soon`}
            className={mergeClasses(
              styles.row,
              activeKey === item.key && styles.rowActive,
              !item.ready && styles.rowDisabled,
            )}
            onClick={() => onChange(item.key)}
            onKeyDown={(e) => handleKey(e, () => onChange(item.key))}
          >
            <span className={styles.icon} aria-hidden>
              {item.icon}
            </span>
            <span className={styles.label}>{item.label}</span>
          </div>
        ))}
    </nav>
  );
};
