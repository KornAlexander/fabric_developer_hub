// PbiFixer — main orchestrator component with tab navigation
// Integrates into AgentHub as a page/tab, using the workload client for auth

import React, { useState, useCallback, useEffect } from "react";
import {
  Tab,
  TabList,
  SelectTabData,
  SelectTabEvent,
  Input,
  Field,
  Button,
  Spinner,
  Text,
  makeStyles,
  shorthands,
} from "@fluentui/react-components";
import {
  DataBarVertical20Regular,
  Document20Regular,
  ArrowSync20Regular,
} from "@fluentui/react-icons";
import { WorkloadClientAPI } from "@ms-fabric/workload-client";
import { ModelExplorer } from "./ModelExplorer";
import { ReportExplorer } from "./ReportExplorer";
import { FONT_FAMILY, BORDER_COLOR, GRAY_COLOR } from "../utils";

const useStyles = makeStyles({
  root: {
    display: "flex",
    flexDirection: "column",
    height: "100%",
    fontFamily: FONT_FAMILY,
  },
  header: {
    display: "flex",
    alignItems: "center",
    ...shorthands.gap("12px"),
    ...shorthands.padding("8px", "12px"),
    ...shorthands.borderBottom("1px", "solid", BORDER_COLOR),
  },
  connectionBar: {
    display: "flex",
    alignItems: "center",
    ...shorthands.gap("8px"),
    ...shorthands.padding("8px", "12px"),
    ...shorthands.borderBottom("1px", "solid", BORDER_COLOR),
    flexWrap: "wrap",
  },
  tabContent: {
    flex: 1,
    ...shorthands.padding("8px", "12px"),
    overflowY: "auto",
    minHeight: 0,
  },
  title: {
    fontSize: "18px",
    fontWeight: "700",
    color: "#333",
  },
  version: {
    fontSize: "11px",
    color: "#999",
  },
  tokenStatus: {
    fontSize: "12px",
    ...shorthands.padding("2px", "8px"),
    ...shorthands.borderRadius("4px"),
  },
});

export interface PbiFixerPageProps {
  workloadClient: WorkloadClientAPI;
}

type TabValue = "model" | "report";

export const PbiFixerPage: React.FC<PbiFixerPageProps> = ({
  workloadClient,
}) => {
  const styles = useStyles();
  const [activeTab, setActiveTab] = useState<TabValue>("model");
  const [workspace, setWorkspace] = useState("");
  const [datasetName, setDatasetName] = useState("");
  const [reportName, setReportName] = useState("");
  const [accessToken, setAccessToken] = useState("");
  const [tokenLoading, setTokenLoading] = useState(false);
  const [tokenError, setTokenError] = useState("");

  const handleTabSelect = useCallback(
    (_: SelectTabEvent, data: SelectTabData) => {
      setActiveTab(data.value as TabValue);
    },
    []
  );

  const handleNavigateToModel = useCallback((_key: string) => {
    setActiveTab("model");
  }, []);

  // Acquire access token via workload client
  const acquireToken = useCallback(async () => {
    setTokenLoading(true);
    setTokenError("");
    try {
      const result = await workloadClient.auth.acquireAccessToken({
        additionalScopesToConsent: [],
        claimsForConditionalAccessPolicy: "",
      });
      if (result?.token) {
        setAccessToken(result.token);
      } else {
        setTokenError("No token returned");
      }
    } catch (err) {
      // Fallback: try to get token from sessionStorage if available
      const cached = sessionStorage.getItem("pbi_fixer_token");
      if (cached) {
        setAccessToken(cached);
      } else {
        setTokenError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      setTokenLoading(false);
    }
  }, [workloadClient]);

  // Auto-acquire token on mount
  useEffect(() => {
    acquireToken();
  }, [acquireToken]);

  return (
    <div className={styles.root}>
      {/* Header */}
      <div className={styles.header}>
        <span className={styles.title}>PBI Fixer</span>
        <span className={styles.version}>TS v0.1.0</span>
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: "8px" }}>
          {tokenLoading && <Spinner size="tiny" />}
          {accessToken && (
            <span className={styles.tokenStatus} style={{ background: "#34c7591a", color: "#34c759" }}>
              Authenticated
            </span>
          )}
          {tokenError && (
            <span className={styles.tokenStatus} style={{ background: "#ff3b301a", color: "#ff3b30" }}>
              {tokenError}
            </span>
          )}
          <Button
            appearance="subtle"
            size="small"
            icon={<ArrowSync20Regular />}
            onClick={acquireToken}
            disabled={tokenLoading}
          >
            Refresh Token
          </Button>
        </div>
      </div>

      {/* Connection bar */}
      <div className={styles.connectionBar}>
        <Field label="Workspace" style={{ flex: "0 0 200px" }}>
          <Input
            value={workspace}
            onChange={(_, data) => setWorkspace(data.value)}
            placeholder="Workspace name or ID"
          />
        </Field>
        <Field label="Semantic Model" style={{ flex: "0 0 200px" }}>
          <Input
            value={datasetName}
            onChange={(_, data) => setDatasetName(data.value)}
            placeholder="Dataset name"
          />
        </Field>
        <Field label="Report" style={{ flex: "0 0 200px" }}>
          <Input
            value={reportName}
            onChange={(_, data) => setReportName(data.value)}
            placeholder="Report name"
          />
        </Field>
      </div>

      {/* Tabs */}
      <TabList selectedValue={activeTab} onTabSelect={handleTabSelect}>
        <Tab value="model" icon={<DataBarVertical20Regular />}>
          Semantic Model
        </Tab>
        <Tab value="report" icon={<Document20Regular />}>
          Report
        </Tab>
      </TabList>

      {/* Tab content */}
      <div className={styles.tabContent}>
        {!accessToken && !tokenLoading && (
          <div style={{ padding: "40px", textAlign: "center", color: GRAY_COLOR }}>
            <Text size={400}>Authentication required. Click &quot;Refresh Token&quot; to connect.</Text>
          </div>
        )}
        {accessToken && activeTab === "model" && (
          <ModelExplorer
            accessToken={accessToken}
            workspace={workspace}
            datasetName={datasetName}
          />
        )}
        {accessToken && activeTab === "report" && (
          <ReportExplorer
            accessToken={accessToken}
            workspace={workspace}
            reportName={reportName}
            onNavigateToModel={handleNavigateToModel}
          />
        )}
      </div>
    </div>
  );
};
