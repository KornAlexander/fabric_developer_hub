// AboutPage — hub-level "About" surface for the Developer Hub.
//
// WL-A (v1.13): the About entry was lifted out of the PBI Fixer
// sub-nav and now lives in the AgentHub shell footer above Support,
// because "about the product" is a hub-wide concern, not a PBI
// Fixer concern.
//
// Content owners:
//   • Lukasz Obst    — creator / maintainer
//   • Alexander Korn — contributor (Microsoft, Solution Engineer DP)
// Credits:
//   • Michael Kovalsky — `semantic-link-labs` (the Python library
//     several PBI Fixer features port from / interoperate with).
//
// Keep this page intentionally lightweight — versions + people +
// links. Anything richer (release notes, license text, telemetry
// opt-out…) belongs on its own dedicated tab.

import * as React from "react";
import {
  makeStyles,
  shorthands,
  tokens,
  Title2,
  Title3,
  Subtitle2,
  Body1,
  Body1Strong,
  Link,
  Divider,
} from "@fluentui/react-components";
import { Open16Regular } from "@fluentui/react-icons";
import { WORKLOAD_VERSION } from "../../version";
import { PBI_FIXER_VERSION } from "../PbiFixer/utils/version";

const useStyles = makeStyles({
  root: {
    height: "100%",
    overflowY: "auto",
    backgroundColor: tokens.colorNeutralBackground1,
  },
  inner: {
    maxWidth: "780px",
    ...shorthands.margin("0", "auto"),
    ...shorthands.padding("32px", "32px", "48px"),
    display: "flex",
    flexDirection: "column",
    rowGap: "20px",
  },
  hero: {
    display: "flex",
    flexDirection: "column",
    rowGap: "8px",
  },
  tagline: {
    color: tokens.colorNeutralForeground2,
  },
  versionRow: {
    display: "flex",
    flexWrap: "wrap",
    columnGap: "12px",
    rowGap: "8px",
    marginTop: "4px",
  },
  badge: {
    display: "inline-flex",
    alignItems: "center",
    columnGap: "6px",
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
    fontSize: "12px",
    color: tokens.colorNeutralForeground2,
    backgroundColor: tokens.colorNeutralBackground3,
    ...shorthands.border("1px", "solid", tokens.colorNeutralStroke2),
    ...shorthands.borderRadius("4px"),
    ...shorthands.padding("3px", "8px"),
  },
  section: {
    display: "flex",
    flexDirection: "column",
    rowGap: "8px",
  },
  list: {
    margin: 0,
    paddingLeft: "20px",
    display: "flex",
    flexDirection: "column",
    rowGap: "6px",
  },
  inlineLink: {
    display: "inline-flex",
    alignItems: "center",
    columnGap: "4px",
  },
  footnote: {
    color: tokens.colorNeutralForeground3,
    fontSize: tokens.fontSizeBase200,
  },
});

export interface AboutPageProps {
  // Reserved for future use (workloadClient, etc.). The hub-level
  // About surface currently needs no runtime context.
}

export const AboutPage: React.FC<AboutPageProps> = () => {
  const styles = useStyles();
  return (
    <div className={styles.root}>
      <div className={styles.inner}>
        {/* Hero */}
        <div className={styles.hero}>
          <Title2>Developer Hub</Title2>
          <Body1 className={styles.tagline}>
            A Microsoft Fabric workload that bundles Power BI productivity
            tools, semantic-model utilities, and an agent-driven session
            workspace into a single editor surface.
          </Body1>
          <div className={styles.versionRow}>
            <span className={styles.badge}>Workload {WORKLOAD_VERSION}</span>
            <span className={styles.badge}>PBI Fixer {PBI_FIXER_VERSION}</span>
          </div>
        </div>

        <Divider />

        {/* Authors */}
        <section className={styles.section}>
          <Title3>Authors</Title3>
          <ul className={styles.list}>
            <li>
              <Body1Strong>Lukasz Obst</Body1Strong>
              <Body1> — creator &amp; maintainer.</Body1>{" "}
              <Link
                href="https://www.linkedin.com/in/lukasz-obst-3672083a2/"
                target="_blank"
                rel="noopener noreferrer"
                className={styles.inlineLink}
              >
                LinkedIn <Open16Regular />
              </Link>
            </li>
            <li>
              <Body1Strong>Alexander Korn</Body1Strong>
              <Body1> — creator &amp; maintainer; Solution Engineer Data Platform, Microsoft.</Body1>{" "}
              <Link
                href="https://www.linkedin.com/in/alexanderkorn/"
                target="_blank"
                rel="noopener noreferrer"
                className={styles.inlineLink}
              >
                LinkedIn <Open16Regular />
              </Link>
            </li>
          </ul>
        </section>

        {/* Acknowledgements */}
        <section className={styles.section}>
          <Title3>Acknowledgements</Title3>
          <ul className={styles.list}>
            <li>
              <Body1Strong>Michael Kovalsky</Body1Strong>
              <Body1>
                {" "}— for{" "}
                <Link
                  href="https://github.com/microsoft/semantic-link-labs"
                  target="_blank"
                  rel="noreferrer"
                  className={styles.inlineLink}
                >
                  semantic-link-labs <Open16Regular />
                </Link>
                , whose Python utilities (Vertipaq Analyzer, BPA helpers, TMDL
                round-trips) inspired several Power BI Fixer features.
              </Body1>
            </li>
            <li>
              <Body1>The </Body1>
              <Link
                href="https://learn.microsoft.com/fabric/workload-development-kit/development-kit-overview"
                target="_blank"
                rel="noreferrer"
                className={styles.inlineLink}
              >
                Microsoft Fabric Workload Development Kit <Open16Regular />
              </Link>
              <Body1> team — for the workload SDK this hub is built on.</Body1>
            </li>
            <li>
              <Body1>The </Body1>
              <Link
                href="https://react.fluentui.dev/"
                target="_blank"
                rel="noreferrer"
                className={styles.inlineLink}
              >
                Fluent UI v9 <Open16Regular />
              </Link>
              <Body1> team — for the design system and component library.</Body1>
            </li>
          </ul>
        </section>

        {/* Resources */}
        <section className={styles.section}>
          <Title3>Resources</Title3>
          <ul className={styles.list}>
            <li>
              <Link
                href="https://github.com/LukaszObst/fabric_developer_hub"
                target="_blank"
                rel="noreferrer"
                className={styles.inlineLink}
              >
                GitHub repository <Open16Regular />
              </Link>
            </li>
            <li>
              <Link
                href="https://github.com/LukaszObst/fabric_developer_hub/blob/main/LICENSE"
                target="_blank"
                rel="noreferrer"
                className={styles.inlineLink}
              >
                License (MIT) <Open16Regular />
              </Link>
            </li>
          </ul>
        </section>

        <Divider />

        <Subtitle2 className={styles.footnote}>
          Built for and inside Microsoft Fabric.
        </Subtitle2>
      </div>
    </div>
  );
};

export default AboutPage;
