/**
 * AgentHub API client — talks to the Python backend.
 *
 * Every function accepts an optional `fabricToken` that is forwarded for
 * OBO token exchange so agents can call Fabric/OneLake tools on behalf
 * of the signed-in user.
 */

import { currentRequestId } from '../utils/correlation';

const BE = process.env.WORKLOAD_BE_URL || '';

interface FetchOpts {
    githubToken?: string;
    fabricToken?: string;
    /** Optional explicit request ID; when omitted, falls back to the
     *  current `withRequestId(...)` scope. Stamped onto `X-Request-ID`
     *  so backend log lines correlate to the originating user action. */
    requestId?: string;
}

function headers(opts: FetchOpts): Record<string, string> {
    const h: Record<string, string> = { 'Content-Type': 'application/json' };
    if (opts.githubToken) h['Authorization'] = `Bearer ${opts.githubToken}`;
    if (opts.fabricToken) h['X-Fabric-Token'] = `Bearer ${opts.fabricToken}`;
    const rid = opts.requestId ?? currentRequestId();
    if (rid) h['X-Request-ID'] = rid;
    return h;
}

// ── Sessions ────────────────────────────────────────────────────────

/** A file attached to a prompt. Content shape depends on kind:
 *  - "text":  raw UTF-8 file contents
 *  - "image": base64 data URI (data:image/...;base64,...)
 *  - "pdf":   base64 data URI (data:application/pdf;base64,...)
 */
export interface PromptAttachment {
    name: string;
    kind: "text" | "image" | "pdf";
    mime?: string;
    content: string;
}

export async function createSession(
    taskDescription: string, workspaceId: string,
    context: Record<string, unknown> | null, opts: FetchOpts,
    attachments?: PromptAttachment[],
    signal?: AbortSignal,
    model?: string | null,
) {
    const res = await fetch(`${BE}/api/sessions`, {
        method: 'POST',
        headers: headers(opts),
        body: JSON.stringify({
            task_description: taskDescription,
            workspace_id: workspaceId,
            context,
            attachments: attachments && attachments.length ? attachments : undefined,
            model: model || undefined,
        }),
        signal,
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
}

/** Entry in the ranked compose-model list returned by
 *  ``GET /api/orchestrate/compose-models``. */
export interface ComposeModelEntry {
    id: string;
    name: string;
    publisher?: string;
    tier: number;
    recommended: boolean;
    top_pick: boolean;
    reason?: string | null;
    latency: "fast" | "medium" | "slow";
}

export interface ComposeModelsResponse {
    models: ComposeModelEntry[];
    default: string | null;
}

/** Fetch the caller's Copilot catalog filtered + ranked for the
 *  compose step. Safe to call unauthenticated-ish: on failure the
 *  backend returns an empty list rather than 500. */
export async function listComposeModels(opts: FetchOpts): Promise<ComposeModelsResponse> {
    const res = await fetch(`${BE}/api/orchestrate/compose-models`, {
        headers: headers(opts),
    });
    if (!res.ok) return { models: [], default: null };
    return res.json();
}

export async function listSessions(
    opts: FetchOpts,
    status?: string,
    page?: { limit?: number; offset?: number },
) {
    const qs = new URLSearchParams();
    if (status) qs.set("status", status);
    if (page?.limit != null) qs.set("limit", String(page.limit));
    if (page?.offset != null) qs.set("offset", String(page.offset));
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    const res = await fetch(`${BE}/api/sessions${suffix}`, { headers: headers(opts) });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
}

export async function getSession(sessionId: string, opts: FetchOpts) {
    const res = await fetch(`${BE}/api/sessions/${sessionId}`, { headers: headers(opts) });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
}

export async function cancelSession(sessionId: string, opts: FetchOpts) {
    const res = await fetch(`${BE}/api/sessions/${sessionId}`, { method: 'DELETE', headers: headers(opts) });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
}

export async function sendMessage(sessionId: string, message: string, targetAgentId: string | null, opts: FetchOpts) {
    const res = await fetch(`${BE}/api/sessions/${sessionId}/message`, {
        method: 'POST',
        headers: headers(opts),
        body: JSON.stringify({ message, target_agent_id: targetAgentId }),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
}

export function subscribeToSessionEvents(sessionId: string): EventSource {
    return new EventSource(`${BE}/api/sessions/${sessionId}/events`);
}

// ── Orchestration ───────────────────────────────────────────────────

export async function compose(
    taskDescription: string,
    workspaceId: string,
    context: Record<string, unknown> | null,
    opts: FetchOpts,
    preferredArchitecture: string | null = null,
) {
    const res = await fetch(`${BE}/api/orchestrate/compose`, {
        method: 'POST',
        headers: headers(opts),
        body: JSON.stringify({
            task_description: taskDescription,
            workspace_id: workspaceId,
            context,
            preferred_architecture: preferredArchitecture,
        }),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
}

export async function runSession(sessionId: string, opts: FetchOpts) {
    const res = await fetch(`${BE}/api/sessions/${sessionId}/run`, {
        method: 'POST',
        headers: headers(opts),
        body: JSON.stringify({ session_id: sessionId }),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
}

export async function rejectComposition(sessionId: string, opts: FetchOpts) {
    const res = await fetch(`${BE}/api/orchestrate/reject`, {
        method: 'POST',
        headers: headers(opts),
        body: JSON.stringify({ session_id: sessionId }),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
}

// Back-compat aliases — retained so component call sites compile during
// the cutover. New code should use `compose`, `runSession`,
// `rejectComposition` directly.
export const generatePlan = (
    taskDescription: string,
    workspaceId: string,
    context: Record<string, unknown> | null,
    opts: FetchOpts,
) => compose(taskDescription, workspaceId, context, opts);
export const approvePlan = runSession;
export const rejectPlan = rejectComposition;

// P4 · Mission Control — resolve a mid-run approval request with one of
// the four recovery actions (approve / decline / request_alternative /
// edit_input).
export async function resolveApproval(
    sessionId: string,
    approvalId: string,
    action: "approve" | "decline" | "request_alternative" | "edit_input",
    reason: string | null,
    opts: FetchOpts,
) {
    const res = await fetch(
        `${BE}/api/sessions/${sessionId}/approvals/${approvalId}`,
        {
            method: 'POST',
            headers: headers(opts),
            body: JSON.stringify({
                session_id: sessionId,
                approval_id: approvalId,
                action,
                reason,
            }),
        },
    );
    if (!res.ok) throw new Error(await res.text());
    return res.json();
}

// ── Agent templates & config ────────────────────────────────────────

export async function listAgentTemplates(opts: FetchOpts) {
    const res = await fetch(`${BE}/api/agents`, { headers: headers(opts) });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
}

export async function getAgentTemplate(agentId: string, opts: FetchOpts) {
    const res = await fetch(`${BE}/api/agents/${agentId}`, { headers: headers(opts) });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
}

export async function configureAgent(config: Record<string, unknown>, opts: FetchOpts) {
    const res = await fetch(`${BE}/api/agents/configure`, {
        method: 'POST',
        headers: headers(opts),
        body: JSON.stringify(config),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
}

export async function listMyAgents(opts: FetchOpts) {
    const res = await fetch(`${BE}/api/agents/my`, { headers: headers(opts) });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
}

export async function deleteMyAgent(configId: string, opts: FetchOpts) {
    const res = await fetch(`${BE}/api/agents/my/${configId}`, { method: 'DELETE', headers: headers(opts) });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
}

// ── Catalogs ────────────────────────────────────────────────────────

export interface ArchitectureEntry {
    id: string;
    name: string;
    headline: string;
    description: string;
    pickWhen: string;
    watchFor: string;
    fabricUseCases: string[];
    hasDriver: boolean;
}

export async function listArchitectures(opts: FetchOpts): Promise<ArchitectureEntry[]> {
    const res = await fetch(`${BE}/api/catalogs/architectures`, { headers: headers(opts) });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
}

// ── Workspaces (cached, with manual refresh) ────────────────────────

export interface Workspace {
    id: string;
    name: string;
    /** null = not yet probed; true = git-connected; false = probed, not connected. */
    git_connected: boolean | null;
    git_provider: string | null;
    git_branch: string | null;
    git_repo_name: string | null;
}

export interface WorkspacesResponse {
    workspaces: Workspace[];
    cached_at: string | null;
    /** "cache" | "refreshed" | "stale-cache" */
    source: string;
}

export async function getWorkspaces(opts: FetchOpts, refresh = false): Promise<WorkspacesResponse> {
    // Dedupe concurrent requests across editor tabs. Every open "New
    // Session" tab mounts its own OrchestratorPage and each one calls
    // this function on init. Without dedupe we'd fan out N identical
    // requests for the same user's workspace list, which means some
    // tabs show "Loading…" long after earlier ones have settled —
    // exactly the flicker the user complained about.
    //
    // Strategy:
    //   1. If a request is in flight, every caller awaits the same
    //      promise.
    //   2. After it resolves, keep the result in memory for a short
    //      TTL so rapid refresh/remount (e.g. closing one tab and
    //      immediately opening another) reuses the cached list
    //      instead of hitting the backend again.
    //
    // ``refresh=true`` bypasses both layers so the explicit "reload"
    // button in the UI still forces a round-trip.
    if (!refresh) {
        if (_wsCache && Date.now() - _wsCache.at < WS_CACHE_TTL_MS) {
            return _wsCache.data;
        }
        if (_wsInflight) return _wsInflight;
    }
    const qs = refresh ? '?refresh=true' : '';
    const p = (async () => {
        const res = await fetch(`${BE}/api/workspaces${qs}`, { headers: headers(opts) });
        if (!res.ok) {
            const err: Error & { status?: number } = new Error(await res.text());
            err.status = res.status;
            throw err;
        }
        const data: WorkspacesResponse = await res.json();
        _wsCache = { at: Date.now(), data };
        return data;
    })();
    _wsInflight = p;
    try {
        return await p;
    } finally {
        // Clear the in-flight slot so a subsequent refresh isn't
        // served stale, but keep _wsCache so tabs opened within the
        // TTL window hit memory instead of the network.
        if (_wsInflight === p) _wsInflight = null;
    }
}

/** Short TTL — long enough to cover "close this tab, open a new one
 *  immediately" without being so long that a genuinely new workspace
 *  the user just created isn't discoverable. Any mutation path in
 *  this module (create / delete) should call ``invalidateWorkspacesCache``. */
const WS_CACHE_TTL_MS = 30_000;
let _wsCache: { at: number; data: WorkspacesResponse } | null = null;
let _wsInflight: Promise<WorkspacesResponse> | null = null;

/** Drop the cached workspace list — called after create/delete
 *  operations so the next read reflects the mutation. */
export function invalidateWorkspacesCache(): void {
    _wsCache = null;
    _wsInflight = null;
}

/** Fire-and-forget background preload after auth. Safe to call without a Fabric token. */
export async function preloadWorkspaces(opts: FetchOpts): Promise<void> {
    try {
        await fetch(`${BE}/api/workspaces/preload`, { method: 'POST', headers: headers(opts) });
    } catch {
        /* best effort */
    }
}

export interface CreateWorkspaceInput {
    display_name: string;
    description?: string;
    capacity_id?: string;
}

export async function createWorkspace(
    input: CreateWorkspaceInput,
    opts: FetchOpts,
): Promise<Workspace> {
    const res = await fetch(`${BE}/api/workspaces`, {
        method: 'POST',
        headers: { ...headers(opts), 'Content-Type': 'application/json' },
        body: JSON.stringify(input),
    });
    if (!res.ok) {
        const err: Error & { status?: number } = new Error(await res.text());
        err.status = res.status;
        throw err;
    }
    // Any subsequent ``getWorkspaces`` must see the new row, so wipe
    // the short-lived read cache. The backend already owns its own
    // cache invalidation; this is purely client-side dedupe hygiene.
    invalidateWorkspacesCache();
    return res.json();
}

export interface WorkspaceItem {
    id: string;
    name: string;
    type: string;
    /** Present on non-folder items that live inside a subfolder. */
    folderId?: string | null;
    /** Present on folder rows when nested inside another folder. */
    parentFolderId?: string | null;
    /** Fabric portal deep link — clickable "View details in a new browser tab". */
    webUrl?: string | null;
    /** Owner / last-modifier display name when surfaced by Fabric's API. */
    owner?: string | null;
}

export interface WorkspaceItemsResponse {
    items: WorkspaceItem[];
    /** ISO-8601 wall-clock timestamp of when the backend fetched the
     *  underlying Fabric data (may be up to ~60s old due to server cache). */
    capturedAt: string;
}

/** List items in a Fabric workspace — powers the workspace preview modal.
 *
 *  Pass ``refresh: true`` to bypass the backend's short-lived cache
 *  (used by the modal's Refresh button so the user always sees the
 *  latest state after adding new items in Fabric).
 */
export async function listWorkspaceItems(
    workspaceId: string,
    opts: FetchOpts & { refresh?: boolean },
): Promise<WorkspaceItemsResponse> {
    const qs = opts.refresh ? "?refresh=1" : "";
    const res = await fetch(
        `${BE}/api/workspaces/${encodeURIComponent(workspaceId)}/items${qs}`,
        { headers: headers(opts) },
    );
    if (!res.ok) {
        const err: Error & { status?: number } = new Error(await res.text());
        err.status = res.status;
        throw err;
    }
    const data = await res.json() as { items: WorkspaceItem[]; captured_at?: string };
    return {
        items: data.items || [],
        capturedAt: data.captured_at || new Date().toISOString(),
    };
}

/** Fire-and-forget preload so the backend's per-(user, workspace) cache
 *  is warm by the time the user clicks the chip. Errors are swallowed —
 *  a real click will surface any real problem.
 */
export function warmWorkspaceItems(workspaceId: string, opts: FetchOpts): void {
    void listWorkspaceItems(workspaceId, opts).catch(() => {});
}

// ── Attachments ─────────────────────────────────────────────────────

/** Mint a single-use download URL for attachment bytes.
 *
 * See the backend's ``agenthub_controller.mint_attachment_download_token``
 * for the "why": the Fabric workload iframe blocks every in-frame save
 * path, so we hand the bytes to the backend and open the resulting
 * http URL via ``workloadClient.navigation.openBrowserTab``.
 *
 * Returns an absolute URL (joined to ``WORKLOAD_BE_URL``) that the
 * caller can pass straight to ``openBrowserTab``.
 */
export async function mintAttachmentDownloadUrl(
    name: string, mime: string, content: string,
    opts: FetchOpts,
): Promise<string> {
    const res = await fetch(`${BE}/api/attachments/download-token`, {
        method: 'POST',
        headers: headers(opts),
        body: JSON.stringify({ name, mime, content }),
    });
    if (!res.ok) throw new Error(`Download-token mint failed: ${res.status} ${await res.text()}`);
    const data = await res.json() as { token: string; url: string };
    // ``data.url`` is relative (``/api/attachments/download/<token>``); we
    // must give ``openBrowserTab`` an absolute URL, so join against the
    // backend origin.
    return `${BE}${data.url}`;
}

