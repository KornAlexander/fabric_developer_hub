// WS-G — translations API client.
// Talks to the AgentHub backend, never directly to Fabric/PBI. The
// propose endpoint returns translation candidates for a single target
// culture at a time; the review grid in `TranslationsPage.tsx` calls
// it once per culture when multiple are selected.

import type { PbiAuth } from "./fabricApi";

// Webpack DefinePlugin replaces the literal ``process.env.WORKLOAD_BE_URL``
// at build time. Guarding the read with ``typeof process !== "undefined"``
// short-circuits to false in the browser bundle and leaves BE as ``""``,
// which then makes fetch() hit the iframe origin (frontend nginx) and
// return 405 on POST. Matches the pattern in ``controller/AgentHubApi.ts``.
const BE: string = process.env.WORKLOAD_BE_URL || "";

export type TranslationObjectType =
  | "Table"
  | "Column"
  | "Measure"
  | "Hierarchy"
  | "Description";

export interface TranslationSourceItem {
  objectType: TranslationObjectType;
  objectPath: string;
  sourceCaption: string;
  existingCaption?: string | null;
}

export interface TranslationProposalItem {
  objectType: TranslationObjectType;
  objectPath: string;
  sourceCaption: string;
  existingCaption?: string | null;
  proposedCaption: string;
  proposedDescription?: string | null;
}

export interface TranslationProposeRequest {
  workspaceId: string;
  datasetId: string;
  targetCultures: string[];
  sourceCulture?: string;
  sourceItems: TranslationSourceItem[];
  glossary?: Record<string, string>;
}

export interface TranslationProposeResponse {
  culture: string;
  items: TranslationProposalItem[];
}

export interface TranslationApplyRequest {
  workspaceId: string;
  datasetId: string;
  culture: string;
  items: TranslationProposalItem[];
}

function headers(auth: PbiAuth): Record<string, string> {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  if (auth.githubToken) h["Authorization"] = `Bearer ${auth.githubToken}`;
  if (auth.fabricToken) h["X-Fabric-Token"] = `Bearer ${auth.fabricToken}`;
  return h;
}

export async function proposeTranslations(
  auth: PbiAuth,
  req: TranslationProposeRequest,
): Promise<TranslationProposeResponse> {
  const res = await fetch(`${BE}/api/pbi-fixer/translations/propose`, {
    method: "POST",
    headers: headers(auth),
    body: JSON.stringify(req),
  });
  // 501 is expected until the LLM bridge is wired — surface cleanly so
  // the UI can show a "not yet" banner instead of a generic error.
  if (res.status === 501) {
    const body = await res.json().catch(() => ({ detail: "Not implemented" }));
    const err = new Error(body.detail || "Translation proposal is not yet enabled") as Error & { status: number };
    err.status = 501;
    throw err;
  }
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`proposeTranslations failed (${res.status}): ${text}`);
  }
  return (await res.json()) as TranslationProposeResponse;
}

export async function applyTranslations(
  auth: PbiAuth,
  req: TranslationApplyRequest,
): Promise<{ applied: number } | { detail: string }> {
  const res = await fetch(`${BE}/api/pbi-fixer/translations/apply`, {
    method: "POST",
    headers: headers(auth),
    body: JSON.stringify(req),
  });
  // 501 is expected for now — surface the message unchanged so the UI
  // can render it as a "not yet" banner instead of a generic error.
  if (res.status === 501) {
    const body = await res.json().catch(() => ({ detail: "Not implemented" }));
    const err = new Error(body.detail || "Translation apply is not yet enabled") as Error & { status: number };
    err.status = 501;
    throw err;
  }
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`applyTranslations failed (${res.status}): ${text}`);
  }
  return await res.json();
}
