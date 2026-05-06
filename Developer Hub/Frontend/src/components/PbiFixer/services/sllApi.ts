// v0.74 — SLL (semantic-link-labs) API client.
//
// Calls the backend's ``/api/pbi-fixer/sll/...`` proxy, which forwards
// to the standalone ``sll-sidecar`` container. This surfaces the
// *literal* output of Michael Kovalsky's ``run_model_bpa`` and
// ``vertipaq_analyzer`` rather than re-implementing the rule engines
// in TypeScript.

import type { PbiAuth } from "./fabricApi";

const BE: string = process.env.WORKLOAD_BE_URL || "";

function headers(auth: PbiAuth): Record<string, string> {
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${auth.githubToken}`,
    "X-Fabric-Token": `Bearer ${auth.fabricToken}`,
  };
}

export interface SllModelBpaResponse {
  /** Column names in the order returned by ``run_model_bpa`` (typically:
   *  Category, Rule Name, Severity, Object Type, Object Name, Description, …). */
  columns: string[];
  /** One row per finding. Values are JSON-friendly strings/numbers/null. */
  rows: Array<Record<string, unknown>>;
}

export interface SllVertipaqResponse {
  /** Concatenated HTML produced by ``IPython.display(HTML(...))``
   *  inside ``vertipaq_analyzer``. Sections are separated by ``<hr/>``. */
  html: string;
}

export async function runSllModelBpa(
  auth: PbiAuth,
  workspace: string,
  dataset: string,
): Promise<SllModelBpaResponse> {
  const res = await fetch(`${BE}/api/pbi-fixer/sll/model-bpa`, {
    method: "POST",
    headers: headers(auth),
    body: JSON.stringify({ workspace, dataset }),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`SLL model-bpa failed (${res.status}): ${text}`);
  }
  return await res.json();
}

export async function runSllVertipaq(
  auth: PbiAuth,
  workspace: string,
  dataset: string,
  readStatsFromData = false,
): Promise<SllVertipaqResponse> {
  const res = await fetch(`${BE}/api/pbi-fixer/sll/vertipaq`, {
    method: "POST",
    headers: headers(auth),
    body: JSON.stringify({
      workspace,
      dataset,
      read_stats_from_data: readStatsFromData,
    }),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`SLL vertipaq failed (${res.status}): ${text}`);
  }
  return await res.json();
}
