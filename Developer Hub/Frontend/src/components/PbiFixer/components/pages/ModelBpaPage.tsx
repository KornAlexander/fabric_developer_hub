// v0.102 — Model BPA was consolidated into the unified "Scan Model"
// panel inside the Model Explorer. This file remains as a thin
// redirect stub so that any cached deep-link (?nav=modelBpa) lands
// on the Model Explorer instead of crashing on a missing route.
//
// PbiFixerPage's `readNavKey` already remaps modelBpa → model on
// page load; this component is the safety net for runtime navigation
// requests that bypass the URL parser.

import React, { useEffect } from "react";
import { Spinner } from "@fluentui/react-components";
import type { PageProps } from "../../types/shared";

export const ModelBpaPage: React.FC<PageProps> = ({ onNavigate }) => {
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
