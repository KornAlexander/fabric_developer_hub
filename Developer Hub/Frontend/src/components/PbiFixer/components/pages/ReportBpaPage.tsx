// v0.102 — Report BPA was consolidated into the unified "Scan Report"
// panel inside the Report Explorer. See ModelBpaPage.tsx for the same
// redirect rationale.

import React, { useEffect } from "react";
import { Spinner } from "@fluentui/react-components";
import type { PageProps } from "../../types/shared";

export const ReportBpaPage: React.FC<PageProps> = ({ onNavigate }) => {
  useEffect(() => {
    onNavigate?.("report");
  }, [onNavigate]);
  return (
    <div style={{ padding: 24, display: "flex", alignItems: "center", gap: 12 }}>
      <Spinner size="tiny" />
      <span>Redirecting to Report Explorer (Scan Report)…</span>
    </div>
  );
};
