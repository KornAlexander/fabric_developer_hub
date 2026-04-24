// WS-E v0.41 — fixers API client.
// Posts to the backend ``/api/pbi-fixer/fixers/apply`` endpoint, which
// runs the actual TMDL / PBIR JSON mutation via Fabric REST and returns
// scan findings (and writes them when ``scanOnly: false``).

import type { PbiAuth } from "./fabricApi";

const BE: string = process.env.WORKLOAD_BE_URL || "";

function headers(auth: PbiAuth): Record<string, string> {
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${auth.githubToken}`,
    "X-Fabric-Token": `Bearer ${auth.fabricToken}`,
  };
}

export interface FixerApplyRequest {
  workspaceId: string;
  fixerId: string;
  scanOnly: boolean;
  datasetId?: string;
  reportId?: string;
}

export interface FixerFindingDto {
  objectPath: string;
  detail?: string | null;
  before?: string | null;
  after?: string | null;
}

export interface FixerApplyResponse {
  fixerId: string;
  scope: "sm" | "report";
  scanOnly: boolean;
  applied: boolean;
  findings: FixerFindingDto[];
  log: string[];
}

export async function runFixer(
  auth: PbiAuth,
  req: FixerApplyRequest,
): Promise<FixerApplyResponse> {
  const res = await fetch(`${BE}/api/pbi-fixer/fixers/apply`, {
    method: "POST",
    headers: headers(auth),
    body: JSON.stringify(req),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`runFixer ${req.fixerId} failed (${res.status}): ${text}`);
  }
  return await res.json();
}
