// Cross-workstream shared types — append-only drop zone.
// WS-A establishes the shape all page components should accept.

import type { PbiAuth } from "../services/fabricApi";
import type { WorkloadClientAPI } from "@ms-fabric/workload-client";

/** Props every Fixer page receives from the shell. Pages may ignore any
 *  field they don't need. When workspace/dataset/report change, the
 *  shell remounts the page (via React `key`) so pages don't need to
 *  reset internal state manually. */
export interface PageProps {
  auth: PbiAuth;
  /** Workload client (for navigation, auth, …). Optional so existing
   *  pages don't have to declare it. */
  workloadClient?: WorkloadClientAPI;
  workspaceId: string;
  /** Name of the selected workspace, if resolved — useful for titles. */
  workspaceName?: string;
  datasetId?: string;
  datasetName?: string;
  reportId?: string;
  reportName?: string;
  /** "Report" or "PaginatedReport" — mirrors Fabric's item type. */
  reportType?: string;
  /** Imperative nav request from a page (e.g. BPA "Fix it" jumps to the
   *  Fixer page). Optional — the shell wires this up. */
  onNavigate?: (key: string) => void;
}
