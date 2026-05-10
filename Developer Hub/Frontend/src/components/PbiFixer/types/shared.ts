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
  /** v0.93 multi-report mode: arrays of every selected dataset/report. In
   *  single mode these contain at most one entry (the current selection).
   *  Pages that haven't migrated yet can keep using `datasetId`/`reportId`
   *  (which is always set to the FIRST array entry) and ignore these. */
  datasetIds?: string[];
  datasetNames?: string[];
  reportIds?: string[];
  reportNames?: string[];
  /** v0.93: true when the user enabled Multi mode in the header. Pages
   *  use this to decide whether to render stacked sections (one per
   *  loaded item) or a single result. */
  multiMode?: boolean;
  /** v0.93: monotonically increasing token bumped every time the user
   *  presses Apply in Multi mode (or auto-fires in Single mode). Pages
   *  can put this in a useEffect dep list to re-run their load on
   *  Apply without remounting. The shell ALSO includes it in the
   *  remount key so pages without their own Apply listener still
   *  remount on Apply. */
  commitToken?: number;
  /** Imperative nav request from a page (e.g. BPA "Fix it" jumps to the
   *  Fixer page). Optional — the shell wires this up. */
  onNavigate?: (key: string) => void;
}
