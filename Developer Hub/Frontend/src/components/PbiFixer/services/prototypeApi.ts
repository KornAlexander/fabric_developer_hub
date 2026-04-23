// WS-M — Prototype API & PBIR skeleton export.
//
// First cut: build a client-side PBIR-lite skeleton describing pages,
// visuals and field bindings. Downloaded as JSON from the browser —
// good enough to feed into a conversion pipeline or document a design.
//
// Uploading the skeleton to a workspace (as a real PBIR report) is
// deferred until the backend `semantic-link-labs` bridge lands.

import type { PbiAuth } from "./fabricApi";

export type VisualType =
  | "card"
  | "table"
  | "matrix"
  | "barChart"
  | "columnChart"
  | "lineChart"
  | "pieChart"
  | "slicer";

export interface FieldRef {
  /** Role hint — "Values" for measures, "Category" / "Legend" / "Axis"
   *  for columns. Free-form, the exporter respects whatever you set. */
  role: string;
  tableName: string;
  /** Column or measure name. */
  propertyName: string;
  kind: "column" | "measure";
}

export interface PrototypeVisual {
  id: string;
  type: VisualType;
  title: string;
  x: number;
  y: number;
  width: number;
  height: number;
  fields: FieldRef[];
}

export interface PrototypePage {
  id: string;
  name: string;
  width: number;
  height: number;
  visuals: PrototypeVisual[];
}

export interface PrototypeDocument {
  version: "pbir-skeleton/1.0";
  reportName: string;
  datasetName?: string;
  datasetId?: string;
  workspaceId?: string;
  pages: PrototypePage[];
}

/** Convert an in-memory Prototype document to a PBIR-lite JSON string. */
export function exportPrototypeToPbir(doc: PrototypeDocument): string {
  return JSON.stringify(doc, null, 2);
}

/** Trigger a browser download of the exported JSON. */
export function downloadJson(filename: string, content: string): void {
  const blob = new Blob([content], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Stub — uploading the PBIR as a new report in the workspace lands
 *  when the backend bridge is wired. */
export async function uploadPrototypeAsReport(
  _auth: PbiAuth,
  _workspaceId: string,
  _doc: PrototypeDocument,
): Promise<{ uploaded: boolean; message: string }> {
  return {
    uploaded: false,
    message:
      "Backend bridge (PBIR upload via sempy-labs / createReport) not yet wired — use Export JSON.",
  };
}
