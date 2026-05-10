// v0.102 — Memory Analyzer was consolidated into the unified "Scan
// Model" panel inside the Model Explorer. See ModelBpaPage.tsx for
// the same redirect rationale.

import React, { useEffect } from "react";
import { Spinner } from "@fluentui/react-components";
import type { PageProps } from "../../types/shared";

export const MemoryPage: React.FC<PageProps> = ({ onNavigate }) => {
  useEffect(() => {
    onNavigate?.("model");
  }, [onNavigate]);
  return (
    <div style={{ padding: 24, display: "flex", alignItems: "center", gap: 12 }}>
      <Spinner size="tiny" />
      <span>Redirecting to Model Explorer (Scan Model)…</span>
    </div>
  );
};
