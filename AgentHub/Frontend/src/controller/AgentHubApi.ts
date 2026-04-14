/**
 * AgentHub API client — talks to the Python backend.
 *
 * Every function accepts an optional `fabricToken` that is forwarded for
 * OBO token exchange so agents can call Fabric/OneLake tools on behalf
 * of the signed-in user.
 */

const BE = process.env.WORKLOAD_BE_URL || '';

interface FetchOpts {
    githubToken?: string;
    fabricToken?: string;
}

function headers(opts: FetchOpts): Record<string, string> {
    const h: Record<string, string> = { 'Content-Type': 'application/json' };
    if (opts.githubToken) h['Authorization'] = `Bearer ${opts.githubToken}`;
    if (opts.fabricToken) h['X-Fabric-Token'] = `Bearer ${opts.fabricToken}`;
    return h;
}

// ── Jobs ────────────────────────────────────────────────────────────

export async function createJob(
    taskDescription: string, workspaceId: string,
    context: Record<string, unknown> | null, opts: FetchOpts,
) {
    const res = await fetch(`${BE}/api/jobs`, {
        method: 'POST',
        headers: headers(opts),
        body: JSON.stringify({ task_description: taskDescription, workspace_id: workspaceId, context }),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
}

export async function listJobs(opts: FetchOpts, status?: string) {
    const qs = status ? `?status=${status}` : '';
    const res = await fetch(`${BE}/api/jobs${qs}`, { headers: headers(opts) });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
}

export async function getJob(jobId: string, opts: FetchOpts) {
    const res = await fetch(`${BE}/api/jobs/${jobId}`, { headers: headers(opts) });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
}

export async function cancelJob(jobId: string, opts: FetchOpts) {
    const res = await fetch(`${BE}/api/jobs/${jobId}`, { method: 'DELETE', headers: headers(opts) });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
}

export async function sendMessage(jobId: string, message: string, targetAgentId: string | null, opts: FetchOpts) {
    const res = await fetch(`${BE}/api/jobs/${jobId}/message`, {
        method: 'POST',
        headers: headers(opts),
        body: JSON.stringify({ message, target_agent_id: targetAgentId }),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
}

export function subscribeToJobEvents(jobId: string): EventSource {
    return new EventSource(`${BE}/api/jobs/${jobId}/events`);
}

// ── Orchestration ───────────────────────────────────────────────────

export async function generatePlan(taskDescription: string, workspaceId: string, context: Record<string, unknown> | null, opts: FetchOpts) {
    const res = await fetch(`${BE}/api/orchestrate/plan`, {
        method: 'POST',
        headers: headers(opts),
        body: JSON.stringify({ task_description: taskDescription, workspace_id: workspaceId, context }),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
}

export async function approvePlan(jobId: string, opts: FetchOpts) {
    const res = await fetch(`${BE}/api/orchestrate/approve`, {
        method: 'POST',
        headers: headers(opts),
        body: JSON.stringify({ job_id: jobId }),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
}

export async function rejectPlan(jobId: string, opts: FetchOpts) {
    const res = await fetch(`${BE}/api/orchestrate/reject`, {
        method: 'POST',
        headers: headers(opts),
        body: JSON.stringify({ job_id: jobId }),
    });
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
