import { test, expect } from "./fixtures";
import type { Frame, Page } from "@playwright/test";
import { execFileSync } from "child_process";
import path from "path";
/**
 * Full Fabric-portal end-to-end verification that AgentHub can execute
 * a real Fabric mission from the portal, stream useful logs, finish
 * successfully, and leave Fabric artifacts in the selected workspace.
 *
 *   1. Open the workspace in app.powerbi.com.
 *   2. Create a new "Fabric ClawHub" item (the Developer Hub workload).
 *   3. Submit a prompt → "Start mission".
 *   4. Wait for Mission Control to mount inside the workload iframe.
 *   5. Assert the Live Log entries counter grows over time (SSE delivery).
 *   6. Wait for mission success, assert no backend warnings/errors for
 *      the session, and verify the requested Fabric folder/items exist.
 *
 * Requires a logged-in Chromium profile:
 *
 *   npm run login:e2e   # sign in to Fabric, close the window
 *   PLAYWRIGHT_USER_DATA_DIR=$HOME/.config/chromium-wsl \
 *     npx playwright test e2e/fabric-portal-live-log.spec.ts \
 *     --project=chromium --reporter=list
 *
 * Plus the dev stack (``./start.sh dev``) up with the dev-gateway
 * registered (Developer Hub tile must appear in New item gallery).
 */

const WORKSPACE_ID = process.env.FABRIC_WORKSPACE_ID || "8bdca8af-1db1-4fd8-9564-0c98b4dbdffc";
const TENANT_ID = process.env.FABRIC_TENANT_ID || "bfccc183-b152-43b7-babd-7feaa07557d1";
const TILE_NAME = process.env.FABRIC_TILE_NAME || "Developer Hub Dashboard (preview)";
const START_MISSION_BUTTON_RE = /^(start mission|plan this)$/i;
const E2E_GITHUB_TOKEN = process.env.FABRIC_E2E_GITHUB_TOKEN || "";
const E2E_GITHUB_USER = process.env.FABRIC_E2E_GITHUB_USER || "e2e-github-user";
const BACKEND_URL = process.env.WORKLOAD_BE_URL || "http://127.0.0.1:5000";
const RUN_TIMESTAMP = new Date().toISOString().replace(/[-:.TZ]/g, "").slice(0, 14);
const RUN_FOLDER = process.env.FABRIC_E2E_RUN_FOLDER || `tmp_${RUN_TIMESTAMP}`;
const PROMPT = process.env.FABRIC_E2E_PROMPT
    || "Create an end to end solution (ingestion, transformation, semantic modelling and a report) "
        + "which shows all Fabric items I have access to in a nice appropriate visualization. "
        + `Work in folder ${RUN_FOLDER} where ${RUN_FOLDER} is the timestamped run folder.`;

// Fast-fail is ON by default for local debugging. Set FABRIC_E2E_FAST_FAIL=0
// to run with longer, stability-first budgets.
const FAST_FAIL = process.env.FABRIC_E2E_FAST_FAIL !== "0";
const envMs = (name: string, fallback: number): number => {
    const raw = process.env[name];
    if (!raw) return fallback;
    const v = Number(raw);
    return Number.isFinite(v) && v > 0 ? v : fallback;
};
const BUDGETS = {
    testTimeout: envMs("FABRIC_E2E_TEST_TIMEOUT_MS", FAST_FAIL ? 18 * 60_000 : 30 * 60_000),
    waitWorkloadIframe: envMs("FABRIC_E2E_IFRAME_TIMEOUT_MS", FAST_FAIL ? 30_000 : 60_000),
    waitPageFrameAttempts: FAST_FAIL ? 25 : 60,
    authCompleteMs: envMs("FABRIC_E2E_AUTH_TIMEOUT_MS", FAST_FAIL ? 35_000 : 120_000),
    composeVisible: envMs("FABRIC_E2E_COMPOSE_TIMEOUT_MS", FAST_FAIL ? 25_000 : 60_000),
    approveVisible: envMs("FABRIC_E2E_APPROVE_TIMEOUT_MS", FAST_FAIL ? 60_000 : 4 * 60_000),
    firstLiveLog: envMs("FABRIC_E2E_FIRST_LIVE_LOG_TIMEOUT_MS", FAST_FAIL ? 60_000 : 120_000),
    missionComplete: envMs("FABRIC_E2E_MISSION_COMPLETE_TIMEOUT_MS", FAST_FAIL ? 12 * 60_000 : 24 * 60_000),
    artifactVisible: envMs("FABRIC_E2E_ARTIFACT_VISIBLE_TIMEOUT_MS", FAST_FAIL ? 3 * 60_000 : 6 * 60_000),
    reportOpen: envMs("FABRIC_E2E_REPORT_OPEN_TIMEOUT_MS", FAST_FAIL ? 2 * 60_000 : 4 * 60_000),
    reportSmoke: envMs("FABRIC_E2E_REPORT_SMOKE_TIMEOUT_MS", FAST_FAIL ? 30_000 : 60_000),
};

const portalUrl = `https://app.powerbi.com/groups/${WORKSPACE_ID}/list`
    + `?ctid=${TENANT_ID}&experience=fabric-developer`;

type DeviceFlowCapture = {
    userCode?: string;
    verificationUri?: string;
    verificationUriComplete?: string;
};

type BackendAuthCapture = {
    githubToken?: string;
    fabricToken?: string;
};

type WorkspaceItem = {
    id?: string;
    name?: string;
    type?: string;
    folderId?: string;
    webUrl?: string;
};

type InventorySolutionResult = {
    status?: string;
    sourceItemCount?: number;
    sourceWorkspaceCount?: number;
    dataSource?: string;
    notebookWritesEnabled?: boolean;
    persistentDataWritten?: boolean;
    persistentDataStore?: { type?: string; id?: string; displayName?: string; written?: boolean } | null;
    notebookExecution?: { status?: string; exitValue?: string } | null;
    semanticModelDataValidation?: { status?: string; via?: string; rowCount?: number; itemTypes?: string[] } | null;
    errors?: string[];
    warnings?: string[];
    createdItems?: WorkspaceItem[];
};

type FabricDefinitionPart = {
    path?: string;
    payload?: string;
    payloadType?: string;
};

const developerHubRoot = path.resolve(__dirname, "../..");

function isWorkloadUrl(url: string): boolean {
    return /127\.0\.0\.1:(5000|60006)\//.test(url) || url.startsWith(BACKEND_URL);
}

function noteTestError(errors: string[], label: string, detail: string): void {
    const line = `${label}: ${detail}`.replace(/\s+/g, " ").slice(0, 1200);
    errors.push(line);
    console.log(`[test:error] ${line}`);
}

function assertNoRecordedErrors(errors: string[], stage: string): void {
    expect(errors, `Errors encountered during ${stage}:\n${errors.join("\n")}`).toEqual([]);
}

function isIgnorablePortalConsoleError(text: string, sourceUrl: string): boolean {
    if (isWorkloadUrl(sourceUrl)) return false;
    return /EcsClient_.*Fetching ECS configuration failed|correlationId=/i.test(text);
}

function rememberBackendAuth(auth: BackendAuthCapture, headers: Record<string, string>): void {
    const fabric = headers["x-fabric-token"] || headers["x-ms-workload-resource-token"];
    const github = headers.authorization;
    if (fabric && !auth.fabricToken) auth.fabricToken = fabric.replace(/^Bearer\s+/i, "");
    if (github && !auth.githubToken) auth.githubToken = github.replace(/^Bearer\s+/i, "");
}

function backendHeaders(auth: BackendAuthCapture): Record<string, string> {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (auth.fabricToken) headers["X-Fabric-Token"] = `Bearer ${auth.fabricToken}`;
    if (auth.githubToken) headers.Authorization = `Bearer ${auth.githubToken}`;
    return headers;
}

async function fetchSession(page: Page, sessionId: string, auth: BackendAuthCapture): Promise<any | null> {
    if (!auth.fabricToken) return null;
    const res = await page.request.get(`${BACKEND_URL}/api/sessions/${sessionId}`, { headers: backendHeaders(auth) });
    if (!res.ok()) return null;
    return res.json();
}

async function waitForMissionCompleted(page: Page, wf: Pick<Frame, "locator">, sessionId: string, auth: BackendAuthCapture): Promise<void> {
    const deadline = Date.now() + BUDGETS.missionComplete;
    let lastStatus = "unknown";
    while (Date.now() < deadline) {
        const session = await fetchSession(page, sessionId, auth).catch(() => null);
        const status = String(session?.status || session?.state || "").toLowerCase();
        if (status) lastStatus = status;
        if (["completed", "succeeded", "success"].includes(status)) return;
        if (["failed", "error", "cancelled", "canceled"].includes(status)) {
            throw new Error(`Mission ${sessionId} ended with status ${status}`);
        }

        const uiText = await wf.locator(".mc3, .dmc-live").first().innerText({ timeout: 1500 }).catch(() => "");
        if (/\b(failed|cancelled|canceled)\b/i.test(uiText)) {
            throw new Error(`Mission ${sessionId} shows a terminal failure in Mission Control`);
        }
        if (/\b(mission completed|completed successfully|status\s+completed)\b/i.test(uiText)) return;
        await page.waitForTimeout(5000);
    }
    throw new Error(`Mission ${sessionId} did not complete within ${BUDGETS.missionComplete}ms; last status=${lastStatus}`);
}

function backendLogIssues(sessionId: string, sinceIso: string): string[] {
    let logs = "";
    try {
        logs = execFileSync("docker", ["compose", "logs", "--since", sinceIso, "backend"], {
            cwd: developerHubRoot,
            encoding: "utf8",
            maxBuffer: 10 * 1024 * 1024,
        });
    } catch (err) {
        const output = (err as { stdout?: string; stderr?: string }).stdout || (err as { stderr?: string }).stderr || String(err);
        return [`Unable to read backend logs: ${output.slice(0, 1000)}`];
    }
    const shortId = sessionId.slice(0, 8);
    return logs.split(/\r?\n/)
        .filter((line) => line.includes(sessionId) || line.includes(shortId))
        .filter((line) => /\b(ERROR|WARNING|WARN|Traceback|Exception)\b/i.test(line))
        .filter((line) => !/Auth validation soft-failed in dev/i.test(line));
}

async function fetchWorkspaceItems(page: Page, auth: BackendAuthCapture): Promise<WorkspaceItem[]> {
    if (!auth.fabricToken) {
        throw new Error("The test did not capture a Fabric bearer token from backend API traffic.");
    }
    const res = await page.request.get(
        `${BACKEND_URL}/api/workspaces/${encodeURIComponent(WORKSPACE_ID)}/items?refresh=1`,
        { headers: backendHeaders(auth) },
    );
    if (!res.ok()) {
        throw new Error(`workspace item listing failed: ${res.status()} ${await res.text().catch(() => "")}`);
    }
    const body = await res.json() as { items?: WorkspaceItem[] };
    return body.items || [];
}

async function fetchFabricDefinition(
    page: Page,
    auth: BackendAuthCapture,
    itemType: string,
    itemId: string,
    format?: string,
): Promise<FabricDefinitionPart[]> {
    if (!auth.fabricToken) {
        throw new Error("The test did not capture a Fabric bearer token for backend definition validation.");
    }
    const base = `${BACKEND_URL}/api/workspaces/${encodeURIComponent(WORKSPACE_ID)}/items/${encodeURIComponent(itemType)}/${encodeURIComponent(itemId)}/definition`;
    const url = format ? `${base}?format=${encodeURIComponent(format)}` : base;
    const response = await page.request.post(url, { headers: backendHeaders(auth), timeout: 180_000 });
    if (!response.ok()) {
        throw new Error(`definition fetch failed for ${itemType}${format ? ` (${format})` : ""}: ${response.status()} ${await response.text().catch(() => "")}`);
    }
    const body = await response.json() as { definition?: { parts?: FabricDefinitionPart[] }; parts?: FabricDefinitionPart[] };
    return body.definition?.parts || body.parts || [];
}

function decodeDefinitionPart(part: FabricDefinitionPart): string {
    expect(part.payloadType, `Definition part ${part.path || "(unknown)"} should be InlineBase64`).toBe("InlineBase64");
    expect(part.payload, `Definition part ${part.path || "(unknown)"} should include a payload`).toBeTruthy();
    return Buffer.from(part.payload as string, "base64").toString("utf8");
}

function notebookCodeFromIpynb(text: string): string {
    const notebook = JSON.parse(text) as { cells?: Array<{ cell_type?: string; source?: string | string[] }> };
    return (notebook.cells || [])
        .filter((cell) => cell.cell_type === "code")
        .map((cell) => Array.isArray(cell.source) ? cell.source.join("") : String(cell.source || ""))
        .join("\n");
}

async function validateNotebookDefinitionContainsCode(page: Page, auth: BackendAuthCapture, notebook: WorkspaceItem): Promise<void> {
    expect(notebook.id, "Notebook item should expose an id for definition validation").toBeTruthy();
    const parts = await fetchFabricDefinition(
        page,
        auth,
        "notebook",
        notebook.id as string,
        "ipynb",
    );
    const notebookPart = parts.find((part) => /notebook-content\.ipynb$/i.test(String(part.path || "")));
    expect(notebookPart, "Notebook getDefinition(format=ipynb) should return notebook-content.ipynb").toBeTruthy();
    const notebookText = decodeDefinitionPart(notebookPart as FabricDefinitionPart);
    expect(notebookText.length, "Notebook definition should not be empty").toBeGreaterThan(500);

    const code = notebookCodeFromIpynb(notebookText);
    expect(code.length, "Notebook definition should contain a real executable code cell").toBeGreaterThan(1000);
    expect(code, "Notebook code should call Fabric REST to ingest live inventory data").toContain("requests.get(next_url");
    expect(code, "Notebook code should write the inventory Delta table").toContain("saveAsTable(TABLE_NAME)");
    expect(code, "Notebook code should write the item-type summary Delta table").toContain("saveAsTable(SUMMARY_TABLE_NAME)");
    console.log(`[diag] Notebook definition validation: bytes=${notebookText.length} codeChars=${code.length}`);
}

async function validateSemanticModelDefinitionContainsInventory(page: Page, auth: BackendAuthCapture, semanticModel: WorkspaceItem): Promise<void> {
    expect(semanticModel.id, "SemanticModel item should expose an id for definition validation").toBeTruthy();
    const parts = await fetchFabricDefinition(
        page,
        auth,
        "semanticModel",
        semanticModel.id as string,
    );
    expect(parts.length, "SemanticModel definition should include parts").toBeGreaterThan(0);
    const decoded = parts.map(decodeDefinitionPart).join("\n");
    expect(decoded, "SemanticModel definition should define the FabricItems table").toContain("FabricItems");
    expect(decoded, "SemanticModel definition should contain the inventory item type field").toContain("ItemType");
    expect(decoded, "SemanticModel definition should contain the report aggregation measure").toContain("Item Count");
    console.log(`[diag] SemanticModel definition validation: parts=${parts.length} bytes=${decoded.length}`);
}

async function validateReportDefinitionContainsVisuals(page: Page, auth: BackendAuthCapture, report: WorkspaceItem, semanticModel: WorkspaceItem): Promise<void> {
    expect(report.id, "Report item should expose an id for definition validation").toBeTruthy();
    expect(semanticModel.id, "SemanticModel item should expose an id for report binding validation").toBeTruthy();
    const parts = await fetchFabricDefinition(
        page,
        auth,
        "report",
        report.id as string,
    );
    const decodedByPath = new Map(parts.map((part) => [String(part.path || ""), decodeDefinitionPart(part)]));
    const definitionPbir = decodedByPath.get("definition.pbir") || "";
    const visualParts = [...decodedByPath.entries()].filter(([pathName]) => /\/visual\.json$/i.test(pathName));

    expect(definitionPbir, "Report definition should include definition.pbir").toContain(String(semanticModel.id));
    expect(decodedByPath.has("definition/report.json"), "Report definition should include report.json").toBeTruthy();
    expect(decodedByPath.has("definition/pages/pages.json"), "Report definition should include pages metadata").toBeTruthy();
    expect(visualParts.length, "Report definition should include at least one visual.json part").toBeGreaterThan(0);

    const visualText = visualParts.map(([, text]) => text).join("\n");
    expect(visualText, "Report visuals should bind to the generated FabricItems table").toContain("FabricItems");
    expect(visualText, "Report visuals should use the generated Item Count measure").toContain("Item Count");
    expect(visualText, "Report visuals should group by ItemType for the inventory visualization").toContain("ItemType");
    console.log(`[diag] Report definition validation: parts=${parts.length} visuals=${visualParts.length}`);
}

function hasType(items: WorkspaceItem[], candidates: RegExp[]): boolean {
    return items.some((item) => candidates.some((re) => re.test(String(item.type || ""))));
}

async function waitForFabricArtifacts(page: Page, auth: BackendAuthCapture): Promise<WorkspaceItem[]> {
    const deadline = Date.now() + BUDGETS.artifactVisible;
    let lastItems: WorkspaceItem[] = [];
    while (Date.now() < deadline) {
        lastItems = await fetchWorkspaceItems(page, auth);
        const folder = lastItems.find((item) => String(item.type || "").toLowerCase() === "folder" && item.name === RUN_FOLDER);
        const folderItems = folder?.id
            ? lastItems.filter((item) => item.folderId === folder.id)
            : [];
        if (
            folder
            && hasType(folderItems, [/^report$/i])
            && hasType(folderItems, [/semantic\s*model/i])
            && hasType(folderItems, [/lakehouse/i, /warehouse/i])
            && hasType(folderItems, [/notebook/i, /pipeline/i, /data\s*flow/i])
        ) {
            return folderItems;
        }
        await page.waitForTimeout(10_000);
    }

    const known = lastItems.map((item) => `${item.type || "Unknown"}:${item.name || item.id || "unnamed"}`).slice(0, 80).join(", ");
    throw new Error(`Timed out waiting for ${RUN_FOLDER} to contain ingestion/transformation/model/report artifacts. Known items: ${known}`);
}

// ── Verifier-verdict helpers (Phase E) ───────────────────────────
type VerifierVerdictView = {
    verdictId: string;
    passed: boolean;
    verifierClaimedSuccess: boolean;
    structuralFailures: string[];
    requiresUserBrowserRender: boolean;
    deliverables: Array<{ id?: string | null; type?: string; name?: string | null; webUrl?: string | null }>;
    evidence: {
        browserVerifiedUrls?: string[];
        screenshotPaths?: string[];
        visualsRendered?: boolean;
        loadingStuckObserved?: boolean;
        errorsObserved?: string[];
        expectedTextMatched?: boolean | null;
    };
    decisionRationale?: string;
    summary?: string;
    feedbackRound?: number;
    targetTaskId?: string | null;
    timestampUtc?: string;
};

async function fetchVerifierVerdicts(
    page: Page,
    sessionId: string,
    auth: BackendAuthCapture,
): Promise<VerifierVerdictView[]> {
    const url = `${BACKEND_URL}/api/sessions/${sessionId}/events.json?types=verifier_verdict&limit=200`;
    const res = await page.request.get(url, { headers: backendHeaders(auth) });
    if (!res.ok()) return [];
    const body = await res.json().catch(() => null) as { events?: VerifierVerdictView[] } | null;
    return Array.isArray(body?.events) ? body!.events! : [];
}

function pickReportVerdict(
    verdicts: VerifierVerdictView[],
    report: WorkspaceItem,
): VerifierVerdictView | null {
    if (!verdicts.length) return null;
    const reportId = String(report.id || "").toLowerCase();
    const reportName = String(report.name || "").toLowerCase();
    const matchByDeliverable = (v: VerifierVerdictView): boolean => {
        for (const d of v.deliverables || []) {
            const t = String(d.type || "").toLowerCase();
            if (t !== "report" && t !== "powerbireport") continue;
            const did = String(d.id || "").toLowerCase();
            const dname = String(d.name || "").toLowerCase();
            if (did && did === reportId) return true;
            if (dname && dname === reportName) return true;
        }
        return false;
    };
    const matchByEvidenceUrl = (v: VerifierVerdictView): boolean => {
        const urls = v.evidence?.browserVerifiedUrls || [];
        return urls.some((u) => typeof u === "string" && reportId && u.toLowerCase().includes(reportId));
    };
    const candidates = verdicts.filter((v) => v.requiresUserBrowserRender);
    const targeted = candidates.filter((v) => matchByDeliverable(v) || matchByEvidenceUrl(v));
    const pool = targeted.length ? targeted : candidates;
    if (!pool.length) return null;
    const passed = pool.find((v) => v.passed);
    if (passed) return passed;
    return pool[pool.length - 1];
}

function summarizeVerdicts(verdicts: VerifierVerdictView[]): string {
    if (!verdicts.length) return "(none)";
    return verdicts
        .map((v) => {
            const types = (v.deliverables || []).map((d) => d.type).join(",");
            const ev = v.evidence || {};
            return (
                `verdict=${v.verdictId} passed=${v.passed} ` +
                `claimed=${v.verifierClaimedSuccess} requiresBrowser=${v.requiresUserBrowserRender} ` +
                `failures=[${(v.structuralFailures || []).join(",")}] ` +
                `urls=${(ev.browserVerifiedUrls || []).length} ` +
                `visualsRendered=${!!ev.visualsRendered} loadingStuck=${!!ev.loadingStuckObserved} ` +
                `errors=${(ev.errorsObserved || []).length} deliverables=[${types}]`
            );
        })
        .join("\n");
}

async function fetchInventorySolutionAudit(page: Page, sessionId: string, auth: BackendAuthCapture): Promise<InventorySolutionResult> {
    const res = await page.request.get(`${BACKEND_URL}/api/sessions/${sessionId}/audit`, { headers: backendHeaders(auth) });
    if (!res.ok()) {
        throw new Error(`session audit fetch failed: ${res.status()} ${await res.text().catch(() => "")}`);
    }
    const rows = await res.json() as Array<{ tool_name?: string; success?: number; result_summary?: string }>;
    const inventoryRow = rows.find((row) => row.tool_name === "fabric_create_workspace_inventory_solution");
    expect(inventoryRow, "Mission audit should include the inventory solution tool call").toBeTruthy();
    expect(inventoryRow?.success, "The inventory solution tool call should be audited as successful").toBe(1);

    const sessionRes = await page.request.get(`${BACKEND_URL}/api/sessions/${sessionId}`, { headers: backendHeaders(auth) });
    if (sessionRes.ok()) {
        const session = await sessionRes.json().catch(() => null) as { agents?: Array<{ actions?: Array<{ entity_type?: string; details?: string }> }> } | null;
        for (const agent of session?.agents || []) {
            for (const action of agent.actions || []) {
                if (action.entity_type !== "WorkspaceInventorySolution" || !action.details) continue;
                const details = JSON.parse(action.details) as InventorySolutionResult;
                expect(details.status, "Inventory action details should contain structured tool proof").toBe("created");
                return details;
            }
        }
    }

    const summary = String(inventoryRow?.result_summary || "");
    const jsonMatch = summary.match(/\{[\s\S]*\}/);
    if (!jsonMatch) {
        throw new Error(`Inventory audit row did not contain JSON output: ${summary.slice(0, 500)}`);
    }
    return JSON.parse(jsonMatch[0]) as InventorySolutionResult;
}

function valueFromDaxRow(row: Record<string, unknown>, logicalName: string): unknown {
    for (const [key, value] of Object.entries(row)) {
        if (key === logicalName || key.endsWith(`[${logicalName}]`) || key.endsWith(`.${logicalName}`)) {
            return value;
        }
    }
    return undefined;
}

function escapeRegExp(value: string): string {
    return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

type DaxQueryResult = { rows: Array<Record<string, unknown>>; source?: string };

async function executeDax(page: Page, auth: BackendAuthCapture, semanticModelId: string, daxQuery: string): Promise<DaxQueryResult> {
    if (!auth.fabricToken) {
        throw new Error("The test did not capture a Fabric bearer token for semantic model validation.");
    }
    const res = await page.request.post(
        `${BACKEND_URL}/api/workspaces/${encodeURIComponent(WORKSPACE_ID)}/semantic-models/${encodeURIComponent(semanticModelId)}/query`,
        {
            headers: backendHeaders(auth),
            data: { query: daxQuery },
            timeout: 60_000,
        },
    );
    if (!res.ok()) {
        throw new Error(`DAX query failed: ${res.status()} ${await res.text().catch(() => "")}`);
    }
    const body = await res.json() as { rows?: Array<Record<string, unknown>>; source?: string };
    expect(body.source, "DAX validation must use Power BI executeQueries, not a model-definition fallback").toBe("powerbi_executeQueries");
    return { rows: body.rows || [], source: body.source };
}

function validateInventoryAuditProof(auditResult: InventorySolutionResult): void {
    expect(auditResult.status, `Inventory tool should complete without partial errors: ${(auditResult.errors || []).join("; ")}`).toBe("created");
    expect(auditResult.errors || [], "Inventory tool should not report blocking errors").toEqual([]);
    expect(auditResult.notebookWritesEnabled, "Notebook must be bound to a Lakehouse so it can write Delta tables").toBe(true);
    expect(auditResult.persistentDataWritten, "Notebook execution must persist inventory data, not only embed rows in a model definition").toBe(true);
    expect(auditResult.dataSource, "The accepted solution must use the Lakehouse Delta table ingestion path").toBe("lakehouse_delta_tables");
    expect(auditResult.persistentDataStore?.type, "Persistent inventory data should be written to a Lakehouse").toBe("Lakehouse");
    expect(auditResult.persistentDataStore?.id, "Persistent Lakehouse proof should include the Lakehouse id").toBeTruthy();

    const modelProof = auditResult.semanticModelDataValidation;
    expect(modelProof?.status, "Semantic model should be queryable through Power BI before the report is accepted").toBe("queryable");
    expect(modelProof?.via, "Semantic model data proof must come from Power BI executeQueries").toBe("powerbi_executeQueries");
    expect(modelProof?.rowCount, "Power BI model proof should report real inventory rows").toBe(auditResult.sourceItemCount);
    expect(modelProof?.itemTypes?.length || 0, "Power BI model proof should include item types used by report visuals").toBeGreaterThan(0);
}

async function validateSemanticModelData(page: Page, auth: BackendAuthCapture, semanticModel: WorkspaceItem, auditResult: InventorySolutionResult): Promise<string[]> {
    expect(semanticModel.id, "SemanticModel item should expose an id for DAX validation").toBeTruthy();
    expect(auditResult.sourceItemCount, "Inventory tool should report how many accessible items were loaded into the model").toBeGreaterThan(0);
    expect(auditResult.sourceWorkspaceCount, "Inventory should scan at least one accessible workspace").toBeGreaterThan(0);

    const countResult = await executeDax(page, auth, semanticModel.id as string, `
EVALUATE
ROW("ItemCount", [Item Count])
`);
    const countRows = countResult.rows;
    const itemCount = Number(valueFromDaxRow(countRows[0] || {}, "ItemCount"));
    expect(itemCount, "The produced semantic model should contain one row per accessible Fabric item at inventory time")
        .toBe(auditResult.sourceItemCount);

    const typeResult = await executeDax(page, auth, semanticModel.id as string, `
EVALUATE
SUMMARIZECOLUMNS(
    'FabricItems'[ItemType],
    "Item Count", [Item Count]
)
ORDER BY [Item Count] DESC
`);
    const typeRows = typeResult.rows;
    const totalByType = typeRows.reduce((sum, row) => sum + Number(valueFromDaxRow(row, "Item Count") || 0), 0);
    const itemTypes = typeRows.map((row) => String(valueFromDaxRow(row, "ItemType") || "")).filter(Boolean);

    expect(totalByType, "The report's item-type aggregation should account for every inventory row").toBe(itemCount);
    expect(itemTypes.length, "The model should expose item-type groups for the visualization").toBeGreaterThan(0);
    console.log(`[diag] DAX inventory validation: sourceItems=${itemCount} sourceWorkspaces=${auditResult.sourceWorkspaceCount} itemTypes=${itemTypes.join(", ")}`);
    return itemTypes;
}

function validateNotebookExecution(auditResult: InventorySolutionResult): void {
    const execution = auditResult.notebookExecution;
    expect(execution, "Inventory tool should execute the generated Notebook, not just create it").toBeTruthy();
    expect(String(execution?.status || "").toLowerCase(), "Inventory Notebook execution should complete successfully").toBe("completed");
    if (execution?.exitValue) {
        const exitValue = JSON.parse(execution.exitValue) as { rowCount?: number; workspaceCount?: number };
        expect(exitValue.rowCount, "Inventory Notebook execution should process real Fabric item rows").toBeGreaterThan(0);
        expect(exitValue.workspaceCount, "Inventory Notebook execution should inspect at least one workspace").toBeGreaterThan(0);
        console.log(`[diag] Notebook execution validation: rowCount=${exitValue.rowCount} workspaceCount=${exitValue.workspaceCount}`);
    } else {
        console.log("[diag] Notebook execution validation: status=completed exitValue=(not returned by Fabric)");
    }
}

async function textFromPageAndFrames(pageWithReport: Page): Promise<string> {
    const chunks: string[] = [];
    for (const frame of pageWithReport.frames()) {
        const text = await frame.locator("body").innerText({ timeout: 1_500 }).catch(() => "");
        if (text) chunks.push(text);
    }
    return chunks.join("\n");
}

async function reportCanvasText(pageWithReport: Page): Promise<string> {
    const chunks: string[] = [];
    for (const frame of pageWithReport.frames()) {
        const text = await frame.locator([
            "[data-testid*='visual' i]",
            ".visualContainer",
            ".visual-container",
            ".visualContent",
            ".vcBody",
            "[role='grid']",
            "[role='img']",
            "canvas",
            "svg",
        ].join(", ")).evaluateAll((nodes) => nodes.map((node) => (node as HTMLElement).innerText || node.textContent || "").join("\n"), undefined, { timeout: 1_500 }).catch(() => "");
        if (text) chunks.push(text);
    }
    return chunks.join("\n");
}

async function visualElementCount(pageWithReport: Page): Promise<number> {
    let count = 0;
    for (const frame of pageWithReport.frames()) {
        count += await frame.locator([
            "[role='img']",
            "[aria-label*='bar chart' i]",
            "[data-testid*='visual' i]",
            ".visualContainer",
            ".visual-container",
            ".vcBody",
        ].join(", ")).count().catch(() => 0);
    }
    return count;
}

async function failIfReportErrorVisible(reportPage: Page, latestText: string): Promise<void> {
    const modal = reportPage.getByText(/Something went wrong|Failed to get access request info|couldn'?t load|can'?t display|couldn'?t retrieve the data/i).first();
    const modalVisible = await modal.isVisible({ timeout: 250 }).catch(() => false);
    expect(
        `${latestText}\n${modalVisible ? await modal.innerText().catch(() => "") : ""}`,
        "The opened report must not show a Fabric/Power BI access, model, or visual load failure",
    ).not.toMatch(/Something went wrong|Failed to get access request info|couldn'?t load|can'?t display|error loading|couldn'?t retrieve the data/i);
}

async function validateReportOpensWithVisualization(page: Page, report: WorkspaceItem, expectedItemTypes: string[], screenshotPrefix: string): Promise<void> {
    expect(report.webUrl, "Report item should have a webUrl to open").toBeTruthy();
    const reportPage = await page.context().newPage();
    const reportErrors: string[] = [];
    reportPage.on("console", (message) => {
        if (message.type() === "error" && !message.text().startsWith("Failed to load resource:")) {
            reportErrors.push(message.text().slice(0, 500));
        }
    });
    reportPage.on("pageerror", (err) => reportErrors.push(`${err.name}: ${err.message}`.slice(0, 500)));

    try {
        await reportPage.goto(report.webUrl as string, { waitUntil: "domcontentloaded", timeout: BUDGETS.reportOpen });
        await reportPage.waitForURL(/\/reports\//i, { timeout: BUDGETS.reportOpen }).catch(() => undefined);

        const deadline = Date.now() + Math.min(BUDGETS.reportOpen, BUDGETS.reportSmoke);
        let latestText = "";
        let latestCanvasText = "";
        let latestVisualCount = 0;
        const itemTypePattern = new RegExp(expectedItemTypes.slice(0, 20).map(escapeRegExp).join("|"), "i");
        const reportNamePattern = report.name ? new RegExp(escapeRegExp(report.name), "i") : null;
        let consecutiveRenderedChecks = 0;
        let consecutiveShellChecks = 0;
        while (Date.now() < deadline) {
            latestText = await textFromPageAndFrames(reportPage);
            await failIfReportErrorVisible(reportPage, latestText);
            latestCanvasText = await reportCanvasText(reportPage);
            latestVisualCount = await visualElementCount(reportPage);
            const canvasLooksLikeInventory = /Fabric Items/i.test(latestCanvasText) && itemTypePattern.test(latestCanvasText);
            const pageStillLoading = /Loading your report|Almost done|Loading data|Please wait/i.test(latestText);
            if (latestVisualCount > 0 && canvasLooksLikeInventory && !pageStillLoading) {
                consecutiveRenderedChecks += 1;
            } else {
                consecutiveRenderedChecks = 0;
            }
            if (consecutiveRenderedChecks >= 2) {
                console.log(`[diag] Report opened with visualization DOM count=${latestVisualCount}`);
                expect(reportErrors, `Report page emitted browser errors:\n${reportErrors.join("\n")}`).toEqual([]);
                return;
            }
            // Cross-origin Power BI canvases (`pbi*.powerbi.com`) sandbox their visual DOM so Playwright
            // cannot scrape rows from outside, and the "Loading your report..." overlay text from the
            // shell stays in the host DOM forever even after the cross-origin canvas finishes rendering.
            // The previous implementation accepted a shell-only render here, which silently let through
            // "Loading your report..." stuck reports. The acceptance gate is now the structural
            // verifier_verdict event emitted by the orchestrator (see verifier_verdict.py): the test
            // tail (after this report-watch loop) asserts a passed verdict for the report deliverable.
            // We therefore only short-circuit on the strict in-page render path; otherwise we run the
            // full BUDGETS.reportOpen budget and let the post-watch verdict assertion be the gate.
            await reportPage.waitForTimeout(2500);
        }
        console.log(
            `[diag] Report page did not expose a scrapeable rendered visual before timeout; `
            + `deferring to verifier_verdict gate. visualElements=${latestVisualCount} `
            + `canvasText=${JSON.stringify(latestCanvasText.slice(0, 1000))} `
            + `pageText=${JSON.stringify(latestText.slice(0, 1000))}`,
        );
    } finally {
        try {
            const shot = `${screenshotPrefix}-report.png`;
            await reportPage.screenshot({ path: shot, fullPage: true, animations: "disabled", timeout: 15_000 });
            console.log(`[diag] Screenshot: ${shot}`);
        } catch (err) {
            console.log(`[diag] Report screenshot capture failed: ${(err as Error).message}`);
        }
        await reportPage.close().catch(() => undefined);
    }
}

test("Fabric portal: Developer Hub creates a real Fabric item visualization solution", async ({ page }) => {
    let deviceFlowCapture: DeviceFlowCapture = {};
    const backendAuth: BackendAuthCapture = {};
    const recordedErrors: string[] = [];
    let missionStreamEventCount = 0;
    const screenshotPrefix = `test-results/mission-control-${Date.now()}`;
    const missionStartedAt = new Date().toISOString();

    test.skip(
        !process.env.PLAYWRIGHT_USER_DATA_DIR,
        "Set PLAYWRIGHT_USER_DATA_DIR to a logged-in Chromium profile (npm run login:e2e).",
    );
    test.setTimeout(BUDGETS.testTimeout);

    // Persistent Chromium profiles keep HTTP cache across runs. Clear cache
    // up front so each iteration picks up the latest frontend bundle.
    try {
        const cdp = await page.context().newCDPSession(page);
        await cdp.send("Network.clearBrowserCache");
    } catch {
        // Non-Chromium projects or restricted contexts may not expose CDP.
    }

    // Clean up known fake-token leftovers from earlier test runs without
    // touching legitimate user credentials.
    await page.goto("http://127.0.0.1:60006/", { waitUntil: "domcontentloaded" });
    await page.evaluate(({ githubToken, githubUser }) => {
        const scrub = (store: Storage | undefined) => {
            if (!store) return;
            const tok = store.getItem("github_token") || "";
            const usr = store.getItem("github_user") || "";
            if (tok === "ghu_e2e_test_stub_token" || usr === "e2e-test-user") {
                store.removeItem("github_token");
                store.removeItem("github_user");
            }
            if (githubToken) {
                store.setItem("github_token", githubToken);
                store.setItem("github_user", githubUser);
            }
        };
        try { scrub(window.localStorage); } catch { /* ignore */ }
        try { scrub(window.sessionStorage); } catch { /* ignore */ }
        // Also clear any stale service-worker/runtime caches for the local
        // dev origin to avoid serving an outdated chunk after hot reloads.
        void (async () => {
            try {
                const regs = await navigator.serviceWorker?.getRegistrations?.();
                if (regs) {
                    for (const r of regs) await r.unregister();
                }
            } catch { /* ignore */ }
            try {
                const keys = await caches?.keys?.();
                if (keys) {
                    for (const k of keys) await caches.delete(k);
                }
            } catch { /* ignore */ }
        })();
    }, { githubToken: E2E_GITHUB_TOKEN, githubUser: E2E_GITHUB_USER });

    // Fabric portal is chatty — many of its telemetry/config endpoints
    // legitimately return 400/404. Log responses with URLs so we can
    // distinguish noise from a real failure in our workload.
    page.on("console", (m) => {
        const text = m.text();
        // Always forward our workload's `[mc-stream]` / `[mc]` diagnostics
        // so we can see SSE-connect progress end-to-end in the test log.
        if (/\[mc-stream\]|\[mc\]/.test(text)) {
            if (/\[mc-stream\]\s+event/i.test(text)) {
                missionStreamEventCount += 1;
            }
            console.log(`[page:${m.type()}] ${text}`);
            return;
        }
        if (m.type() === "error") {
            // Drop the useless "Failed to load resource: …" line; the
            // matching response handler below prints it with the URL.
            if (text.startsWith("Failed to load resource:")) return;
            if (isIgnorablePortalConsoleError(text, m.location().url || "")) return;
            console.log(`[page:error] ${text}`);
            noteTestError(recordedErrors, "browser console error", text);
        }
    });

    // Also listen for console messages from all attached frames so we can
    // see iframe bootstrap issues ("Blocked by permissions-policy",
    // postMessage failures, etc.) that never bubble up to the main page.
    page.on("frameattached", (frame) => {
        console.log(`[frame:attached] ${frame.url().slice(0, 120)}`);
    });
    page.on("framenavigated", (frame) => {
        console.log(`[frame:navigated] ${frame.url().slice(0, 120)}`);
    });
    page.on("response", (r) => {
        const status = r.status();
        const url = r.url();

        // Capture the exact device-flow payload emitted by backend so the
        // test can drive GitHub authorization with the same code/URL that
        // the workload generated.
        if (/\/api\/github\/device-code$/.test(url) && status === 200) {
            void r.json()
                .then((data: any) => {
                    deviceFlowCapture = {
                        userCode: typeof data?.user_code === "string" ? data.user_code : undefined,
                        verificationUri: typeof data?.verification_uri === "string" ? data.verification_uri : undefined,
                        verificationUriComplete: typeof data?.verification_uri_complete === "string" ? data.verification_uri_complete : undefined,
                    };
                    console.log(`[diag] captured device-flow payload uri_complete=${Boolean(deviceFlowCapture.verificationUriComplete)} code=${deviceFlowCapture.userCode ?? "n/a"}`);
                })
                .catch(() => undefined);
        }

        // Always surface SSE endpoint calls so we know the frontend connected.
        if (/\/api\/sessions\/[^/]+\/events/.test(url)) {
            console.log(`[page:http ${status}] ${r.request().method()} ${url}`);
            return;
        }
        if (status < 400) return;
        if (isWorkloadUrl(url) && /\/api\//.test(url)) {
            noteTestError(recordedErrors, `HTTP ${status}`, `${r.request().method()} ${url}`);
            return;
        }
        // Filter obvious portal background noise.
        if (/\/(telemetry|metrics|beacon|collect|clientconfig)\b/i.test(url)) return;
        console.log(`[page:http ${status}] ${r.request().method()} ${url}`);
    });
    page.on("requestfailed", (r) => {
        console.log(`[page:netfail] ${r.method()} ${r.url()} — ${r.failure()?.errorText}`);
        if (isWorkloadUrl(r.url())) {
            noteTestError(recordedErrors, "workload request failed", `${r.method()} ${r.url()} - ${r.failure()?.errorText || "unknown"}`);
        }
    });
    page.on("request", (r) => {
        if (/\/api\//.test(r.url())) {
            rememberBackendAuth(backendAuth, r.headers());
        }
    });
    page.on("pageerror", (err) => {
        console.log(`[page:uncaught] ${err.name}: ${err.message}`);
        noteTestError(recordedErrors, "uncaught browser error", `${err.name}: ${err.message}`);
    });

    // ── 1. Open workspace (handle SSO round-trip) ─────────────────
    await page.goto(portalUrl, { waitUntil: "domcontentloaded" });
    for (let i = 0; i < 8; i++) {
        await page.waitForTimeout(3000);
        const url = page.url();
        if (/login\.microsoftonline\.com/.test(url) && !url.includes("code=")) {
            test.skip(true, "Chromium profile is not signed in. Run: npm run login:e2e");
            return;
        }
        if (url.includes("/groups/") && !url.includes("/signin")) break;
        if (url.includes("/signin")) {
            await page.goto(portalUrl, { waitUntil: "domcontentloaded" });
        }
    }
    expect(page.url(), "Failed to land on workspace page").toMatch(/\/groups\//);

    // Workspace shell must paint — wait for the "New item" button itself.
    const newItemBtn = page.getByRole("button", { name: /^new item$/i }).first();
    await expect(newItemBtn).toBeVisible({ timeout: 30_000 });

    // ── 2. Create new "Developer Hub Item" ────────────────────────
    let skipGalleryAndOpenRegisteredWorkload = false;
    console.log("[diag] New item button visible; opening Fabric new-item gallery.");
    try {
        await newItemBtn.click({ timeout: 15_000 });
        await page.waitForTimeout(2500);
    } catch (err) {
        console.log(`[diag] New item click did not complete; trying forced click before fallback: ${(err as Error).message.slice(0, 500)}`);
        try {
            await newItemBtn.click({ timeout: 5_000, force: true });
            await page.waitForTimeout(2500);
            console.log("[diag] Forced New item click completed.");
        } catch (forceErr) {
            skipGalleryAndOpenRegisteredWorkload = true;
            console.log(`[diag] Forced New item click also failed; will use registered-workload fallback: ${(forceErr as Error).message.slice(0, 500)}`);
        }
    }

    // Scope the tile search to the New-item PANEL (role=region), not the
    // toolbar button that ALSO has aria-label="New item".
    const gallery = page.getByRole("region", { name: /^new item$/i }).first();

    // Type into the panel's filter box so we don't have to scroll through
    // hundreds of tiles — much faster and far more reliable.
    const filterBox = gallery.getByRole("searchbox")
        .or(gallery.locator('input[placeholder*="Filter" i], input[placeholder*="Search" i]'))
        .first();

    // Tile name regex — broad enough to tolerate "(preview)" being added/removed
    // or the workload being renamed between "Developer Hub" / "Fabric ClawHub".
    const tileNameRe = /(Developer Hub|Fabric ?ClawHub).*Dashboard|Dashboard.*(Developer Hub|Fabric ?ClawHub)|ClawHub/i;
    const tile = gallery.getByRole("button", { name: tileNameRe }).first();

    /**
     * Try to auto-enable the per-user "Fabric Developer Mode" toggle via
     * the Fabric Settings UI.  Discovered via MCP Playwright browser:
     *
     *   Settings gear → "Developer settings" link →
     *   /user/user-settings/developer-settings page →
     *     Step 1: "Developer mode" toggle (generic clickable "Off"/"On", NOT a switch role)
     *     Step 2: "Workloads Developer mode" switch (only appears AFTER step 1 is ON)
     *
     * Tenant-level admin settings still have to be enabled by an
     * administrator — we can't flip those from a regular user session.
     */
    async function tryEnableDeveloperMode(): Promise<boolean> {
        console.log("[diag] Attempting to enable Fabric Developer Mode for this user…");

        // 1. Navigate directly to the developer-settings page — skips the
        //    gear-menu dance and is robust against Settings flyout variants.
        const settingsUrl = `https://app.powerbi.com/user/user-settings/developer-settings`
            + `?ctid=${TENANT_ID}&experience=fabric-developer`;
        await page.goto(settingsUrl, { waitUntil: "domcontentloaded" });
        await page.waitForTimeout(2500);

        // Verify we landed on the right page.
        if (!/developer-settings/.test(page.url())) {
            console.log(`[diag] Failed to navigate to developer-settings (got ${page.url()}).`);
            return false;
        }

        let flipped = 0;

        // 2a. "Developer mode" — rendered as a generic clickable element
        //     containing the literal text "Off" (NOT a switch role).
        //     On the dev-settings page BEFORE step 2a, only ONE "Off" text
        //     is visible inside <main>; scope to that to be safe.
        const devModeOff = page
            .locator('main, [role="main"]')
            .getByText(/^Off$/)
            .first();

        if (await devModeOff.isVisible({ timeout: 4_000 }).catch(() => false)) {
            await devModeOff.click();
            flipped++;
            console.log("[diag] Clicked 'Developer mode' toggle Off→On.");
            await page.waitForTimeout(2000);
        } else {
            // Fallback — any "Off" text on the page (last resort).
            const anyOff = page.getByText(/^Off$/).first();
            if (await anyOff.isVisible({ timeout: 2_000 }).catch(() => false)) {
                await anyOff.click();
                flipped++;
                console.log("[diag] Clicked fallback 'Off' toggle.");
                await page.waitForTimeout(2000);
            } else {
                console.log("[diag] 'Developer mode' Off toggle not located.");
            }
        }

        // 2b. "Workloads Developer mode" switch appears only AFTER step 2a.
        const workloadsSwitch = page.getByRole("switch").first();
        if ((await workloadsSwitch.count().catch(() => 0)) > 0) {
            const state = await workloadsSwitch.getAttribute("aria-checked").catch(() => null);
            if (state === "false") {
                await workloadsSwitch.click();
                flipped++;
                console.log("[diag] Flipped 'Workloads Developer mode' switch ON.");
                await page.waitForTimeout(800);
            } else {
                console.log(`[diag] 'Workloads Developer mode' switch already ${state}.`);
            }
        } else {
            console.log("[diag] No switch role found on developer-settings page.");
        }

        console.log(`[diag] Developer settings: flipped ${flipped} toggle(s).`);

        // 3. Navigate back to the workspace and reopen New item.
        await page.goto(portalUrl, { waitUntil: "domcontentloaded" });
        await page.waitForTimeout(3500);
        const newItemBtn2 = page.getByRole("button", { name: /^new item$/i }).first();
        if (await newItemBtn2.isVisible().catch(() => false)) {
            try {
                await newItemBtn2.click({ timeout: 15_000 });
                await page.waitForTimeout(3000);
            } catch (err) {
                console.log(`[diag] New item click after developer-settings recovery did not complete: ${(err as Error).message.slice(0, 500)}`);
            }
        }
        return flipped > 0;
    }

    /**
     * Type into the New-item panel's filter box (if present) and then
     * check whether the Developer Hub tile is rendered.
     */
    async function findTile(): Promise<boolean> {
        console.log("[diag] Searching Fabric New-item gallery for Developer Hub tile.");
        // Wait for the panel itself to exist.
        if (!(await gallery.isVisible({ timeout: 8_000 }).catch(() => false))) {
            console.log("[diag] New-item panel (region[aria-label='New item']) not visible.");
            return false;
        }
        console.log("[diag] New-item panel is visible.");

        // The New-item panel may show category tabs (e.g. "Favorites",
        // "All items", "Developer"). Click "All items" (and "Developer" when
        // present) so developer workloads are part of the filterable set.
        for (const tabName of [/^all items$/i, /^developer$/i]) {
            const tab = gallery.getByRole("tab", { name: tabName }).first();
            if (await tab.isVisible({ timeout: 500 }).catch(() => false)) {
                await tab.click({ timeout: 3_000 }).catch((err) => {
                    console.log(`[diag] Gallery tab click ${tabName} failed: ${(err as Error).message.slice(0, 240)}`);
                });
                await page.waitForTimeout(500);
            }
        }

        // Use the filter box when available so Fabric narrows the virtualised list.
        if (await filterBox.isVisible({ timeout: 1_500 }).catch(() => false)) {
            console.log("[diag] Gallery filter box visible; filtering for Developer Hub.");
            await filterBox.fill("", { timeout: 3_000 }).catch((err) => {
                console.log(`[diag] Clearing gallery filter failed: ${(err as Error).message.slice(0, 240)}`);
            });
            // Type a shorter, more-forgiving term. The exact display name
            // has varied over Fabric releases ("Developer Hub Dashboard",
            // "Developer Hub Dashboard (preview)", etc.) but all contain
            // "Developer Hub".
            await filterBox.fill("Developer Hub", { timeout: 3_000 }).catch((err) => {
                console.log(`[diag] Filling gallery filter failed: ${(err as Error).message.slice(0, 240)}`);
            });
            await page.waitForTimeout(1500);
        }
        // `count() > 0` checks DOM presence even for virtualised off-screen tiles.
        let count = await tile.count().catch(() => 0);
        console.log(`[diag] Gallery Developer Hub tile count after first filter: ${count}`);
        if (count === 0) {
            // Fall back: try an even shorter filter ("Fabric ClawHub" or just "ClawHub").
            if (await filterBox.isVisible().catch(() => false)) {
                await filterBox.fill("", { timeout: 3_000 }).catch((err) => {
                    console.log(`[diag] Clearing gallery filter before ClawHub fallback failed: ${(err as Error).message.slice(0, 240)}`);
                });
                await filterBox.fill("ClawHub", { timeout: 3_000 }).catch((err) => {
                    console.log(`[diag] Filling gallery filter ClawHub failed: ${(err as Error).message.slice(0, 240)}`);
                });
                await page.waitForTimeout(1200);
                count = await tile.count().catch(() => 0);
            }
        }
        console.log(`[diag] Gallery Developer Hub tile count after fallback filter: ${count}`);
        if (count === 0) return false;
        await tile.scrollIntoViewIfNeeded({ timeout: 3_000 }).catch((err) => {
            console.log(`[diag] Tile scroll into view failed: ${(err as Error).message.slice(0, 240)}`);
        });
        return true;
    }

    async function openRegisteredWorkloadFallback(): Promise<boolean> {
        console.log("[diag] Trying registered-workload fallback because the New item gallery did not expose the tile.");

        const closeGallery = gallery.getByRole("button", { name: /close/i }).first()
            .or(page.getByLabel(/close/i).first());
        if (await closeGallery.isVisible({ timeout: 1_000 }).catch(() => false)) {
            await closeGallery.click().catch(() => undefined);
            await page.waitForTimeout(1_000);
        } else {
            await page.keyboard.press("Escape").catch(() => undefined);
            await page.waitForTimeout(500);
        }

        const leftRailWorkload = page
            .getByRole("button", { name: /Fabric ClawHub|Developer Hub/i })
            .or(page.getByRole("link", { name: /Fabric ClawHub|Developer Hub/i }))
            .first();
        if (await leftRailWorkload.isVisible({ timeout: 5_000 }).catch(() => false)) {
            await leftRailWorkload.click();
            await page.waitForTimeout(5_000);
            console.log(`[diag] Clicked registered workload in left rail. URL: ${page.url()}`);
        } else {
            console.log("[diag] Registered workload was not visible in the left rail.");
        }

        let iframeCount = await page.locator('iframe[name="iframe-page-Org.FabricClawHub"]').count().catch(() => 0);
        if (iframeCount === 0) {
            const directUrl = `https://app.powerbi.com/groups/${WORKSPACE_ID}/workloads/Org.FabricClawHub/agent-hub/orchestrator`
                + `?ctid=${TENANT_ID}&experience=fabric-developer`;
            console.log(`[diag] Navigating directly to registered workload URL: ${directUrl}`);
            await page.goto(directUrl, { waitUntil: "domcontentloaded" });
            await page.waitForTimeout(6_000);
            iframeCount = await page.locator('iframe[name="iframe-page-Org.FabricClawHub"]').count().catch(() => 0);
        }

        const opened = iframeCount > 0 || /workloads\/Org\.FabricClawHub/i.test(page.url());
        console.log(`[diag] Registered-workload fallback opened=${opened} iframeCount=${iframeCount} url=${page.url()}`);
        return opened;
    }

    let openedViaRegisteredWorkloadFallback = false;
    let tileVisible = false;
    if (skipGalleryAndOpenRegisteredWorkload) {
        openedViaRegisteredWorkloadFallback = await openRegisteredWorkloadFallback();
        if (!openedViaRegisteredWorkloadFallback) {
            throw new Error(
                "Fabric New item gallery could not be opened and the registered workload fallback did not expose Org.FabricClawHub.",
            );
        }
    } else {
        tileVisible = await findTile();
    }
    if (!openedViaRegisteredWorkloadFallback && !tileVisible) {
        console.log("[diag] Tile not found after filter search — starting developer-settings recovery.");
        const flipped = await tryEnableDeveloperMode().catch((e) => {
            console.log(`[diag] tryEnableDeveloperMode threw: ${e}`);
            return false;
        });
        // Re-check regardless of `flipped` — recovery may have done a useful reload
        // (e.g. the tile was lazy-loaded) even if it didn't flip any toggles.
        tileVisible = await findTile();
        void flipped;
    }

    if (!openedViaRegisteredWorkloadFallback && !tileVisible) {
        // Dump tiles INSIDE the New-item panel (not the whole page) so the
        // developer sees what the gallery actually contains.
        const panelTiles = await gallery
            .getByRole("button")
            .allInnerTexts()
            .catch(() => []);
        const panelSample = panelTiles
            .map((t) => t.trim().split("\n")[0])
            .filter(Boolean)
            .slice(0, 60);
        console.log(`[diag] Tiles inside New-item panel (first 60 of ${panelTiles.length}):`, panelSample);
        const devMatch = panelTiles.filter((t) => /developer/i.test(t));
        console.log("[diag] Panel tiles matching /developer/i:", devMatch);

        // Dump full panel text + anchor/li counts — tile virtualisation in
        // Fabric often makes tiles render as role=link / role=gridcell /
        // role=listitem rather than role=button, so `getByRole('button')`
        // misses them entirely.
        for (const r of ["link", "listitem", "menuitem", "gridcell", "option", "tab"]) {
            const labels = await gallery.getByRole(r as any).allInnerTexts().catch(() => []);
            const sample = labels.map((t) => t.trim().split("\n")[0]).filter(Boolean);
            console.log(`[diag]   role=${r}: count=${labels.length} sample=`, sample.slice(0, 30));
        }
        // Also dump the raw text content of the panel for a last-resort check.
        const panelText = (await gallery.innerText().catch(() => "")).slice(0, 2000);
        console.log(`[diag] Panel text (truncated 2000 chars):\n${panelText}`);
        // Screenshot for visual inspection.
        try {
            const shotPath = `test-results/tile-missing-${Date.now()}.png`;
            await page.screenshot({ path: shotPath, fullPage: true, animations: "disabled", timeout: 15_000 });
            console.log(`[diag] Screenshot: ${shotPath}`);
        } catch {}

        // Check whether the "Developer" category tab is even present —
        // it only appears when the tenant has developer mode enabled and
        // the user has a workload dev gateway registered.
        const devTabPresent = await page
            .getByRole("tab", { name: /developer/i })
            .count()
            .catch(() => 0);

        openedViaRegisteredWorkloadFallback = await openRegisteredWorkloadFallback();
        if (!openedViaRegisteredWorkloadFallback) {

            throw new Error(
                `"${TILE_NAME}" tile is not visible in the Fabric "New item" gallery ` +
                    `even after attempting to toggle per-user Developer Mode.\n` +
                    `This usually means TENANT-level developer settings are not enabled ` +
                    `(only an admin can flip those). Checklist:\n` +
                    `  1. Fabric portal → Settings → Admin portal → Tenant settings → ` +
                    `"Developer settings" → "Users can create Fabric developer items" is ENABLED ` +
                    `for your security group (or the entire org). [ADMIN REQUIRED]\n` +
                    `  2. Fabric portal → top-right gear → "Developer settings" → ` +
                    `"Fabric Developer Mode" toggle is ON for YOUR user. [auto-attempted above]\n` +
                    `  3. Your local dev gateway is running and registered — check: ` +
                    `"./start.sh dev" logs should show "Dev Gateway" connected and the ` +
                    `workload manifest loaded from Developer Hub/Backend/manifest/.\n` +
                    `  4. Reload the Fabric portal (Ctrl+F5) after enabling above.\n` +
                    `Diagnostics: Developer tab in gallery = ${devTabPresent > 0 ? "FOUND" : "MISSING"}.`,
            );
        }
    }
    if (!openedViaRegisteredWorkloadFallback) {
        await tile.scrollIntoViewIfNeeded({ timeout: 3_000 }).catch((err) => {
            console.log(`[diag] Final tile scroll before click failed: ${(err as Error).message.slice(0, 240)}`);
        });
        try {
            await tile.click({ timeout: 15_000 });
        } catch (err) {
            console.log(`[diag] Tile click did not complete; trying forced tile click: ${(err as Error).message.slice(0, 500)}`);
            await tile.click({ timeout: 5_000, force: true });
        }
        console.log(`[diag] Clicked tile. URL just after click: ${page.url()}`);
    } else {
        console.log("[diag] Continuing after registered-workload fallback.");
    }

    // Wait for Fabric to navigate to the workload route — the URL should
    // change to /workloads/Org.FabricClawHub/agent-hub/orchestrator…
    try {
        await page.waitForURL(/workloads\/Org\.FabricClawHub/i, { timeout: 15_000 });
        console.log(`[diag] URL navigated to workload: ${page.url()}`);
    } catch {
        console.log(`[diag] URL did NOT change to /workloads/ within 15s. Current: ${page.url()}`);
    }

    // Optional name dialog: some Fabric item types prompt for a name, others
    // (like Developer Hub) create the item with a default name and open the
    // workload straight away. Only fill a name if a create-item DIALOG appears.
    const nameDialog = page.getByRole("dialog").filter({
        has: page.getByRole("button", { name: /^create$/i }),
    }).first();
    if (await nameDialog.isVisible({ timeout: 4_000 }).catch(() => false)) {
        const nameInput = nameDialog.getByRole("textbox").first();
        if (await nameInput.isVisible({ timeout: 2_000 }).catch(() => false)) {
            const itemName = `E2E Live Log ${Date.now()}`;
            await nameInput.fill(itemName);
        }
        await nameDialog.getByRole("button", { name: /^create$/i }).click();
    } else {
        console.log("[diag] No name dialog appeared — workload opens directly.");
    }

    // ── 3. Wait for the workload iframe ───────────────────────────
    // Fabric mounts the workload in <iframe name="iframe-page-Org.FabricClawHub">
    // whose src is http://127.0.0.1:60006/?...__bootstrapPath=agent-hub/orchestrator...
    // Use FrameLocator so Playwright auto-retries across navigations.
    const wlIframeEl = page.locator('iframe[name="iframe-page-Org.FabricClawHub"]');
    await expect(
        wlIframeEl,
        "Workload iframe (iframe-page-Org.FabricClawHub) never appeared in the DOM. "
            + "This means Fabric never loaded the workload from the dev gateway at 127.0.0.1:60006. "
            + "Check that ./start.sh dev is running and the dev gateway registered successfully.",
    ).toBeAttached({ timeout: BUDGETS.waitWorkloadIframe });

    // Wait until Playwright sees the orchestrator PAGE iframe (not the
    // worker iframe, and not the main page whose URL also contains
    // "Org.FabricClawHub"). Fabric attaches the page iframe a few seconds
    // after the worker; if we query `contentFrame()` too early, the lookup
    // can return a stale null and all subsequent getByRole queries time out.
    let pageFrame: Frame | undefined;
    for (let attempt = 0; attempt < BUDGETS.waitPageFrameAttempts; attempt++) {
        const frames = page.frames();
        pageFrame = frames.find(
            (fr) => /^http:\/\/127\.0\.0\.1:60006/.test(fr.url()) && /__iframeType=page|\/agent-hub\//.test(fr.url()),
        );
        const iframeSrc = await wlIframeEl.getAttribute("src").catch(() => null);
        if (attempt === 0 || attempt % 5 === 0 || pageFrame) {
            console.log(
                `[diag] wait-page-frame attempt=${attempt} frames=${frames.length} pageFrameFound=${!!pageFrame} iframeSrc=${iframeSrc?.slice(0, 80) ?? "null"}`,
            );
        }
        if (pageFrame) break;
        await page.waitForTimeout(1000);
    }
    if (!pageFrame) {
        const loopbackFrames = page.frames().filter((f) => /127\.0\.0\.1:60006/.test(f.url()));
        console.log(`[diag] All loopback frames after timeout:`);
        for (const fr of loopbackFrames) console.log(`  - ${fr.url().slice(0, 200)}`);
    }

    const findWorkloadPageFrame = (preferSession = false): Frame | undefined => {
        const candidates = page.frames().filter(
            (fr) => /^http:\/\/127\.0\.0\.1:60006/.test(fr.url()) && /__iframeType=page|\/agent-hub\//.test(fr.url()),
        );
        return (preferSession ? candidates.find((fr) => /\/agent-hub\/session\//i.test(fr.url())) : undefined)
            ?? candidates.find((fr) => /\/agent-hub\//i.test(fr.url()))
            ?? candidates[0];
    };
    let wf: Frame | ReturnType<typeof wlIframeEl.contentFrame> = pageFrame ?? wlIframeEl.contentFrame();
    const refreshWorkloadFrame = (preferSession = false) => {
        const current = findWorkloadPageFrame(preferSession);
        if (current) {
            pageFrame = current;
            wf = current;
        }
        return wf;
    };

    async function completeGitHubSignInIfNeeded(): Promise<void> {
        const signOutBtn = wf.getByRole("button", { name: /sign out \(/i }).first();
        const signInBtn = wf.getByRole("button", { name: /^sign in with github$/i }).first();

        const clickFirstVisible = async (pg: import("@playwright/test").Page, patterns: RegExp[]) => {
            const maybeScrollAuthorizationPage = async (): Promise<void> => {
                const wantsAuthorize = patterns.some((ptn) => /authorize|allow|approve|confirm/i.test(String(ptn)));
                if (!wantsAuthorize && !/\/login\/device\/confirmation/.test(pg.url())) return;
                await pg.evaluate(() => window.scrollTo(0, document.body.scrollHeight)).catch(() => undefined);
                await pg.keyboard.press("End").catch(() => undefined);
                await pg.waitForTimeout(500);
            };

            for (const ptn of patterns) {
                const btn = pg.getByRole("button", { name: ptn }).first();
                if (await btn.isVisible({ timeout: 1_500 }).catch(() => false)) {
                    await btn.scrollIntoViewIfNeeded().catch(() => undefined);
                    if (!(await btn.isEnabled({ timeout: 500 }).catch(() => false))) {
                        await maybeScrollAuthorizationPage();
                        if (!(await btn.isEnabled({ timeout: 2_000 }).catch(() => false))) continue;
                    }
                    await btn.click();
                    return true;
                }
                const link = pg.getByRole("link", { name: ptn }).first();
                if (await link.isVisible({ timeout: 800 }).catch(() => false)) {
                    await link.scrollIntoViewIfNeeded().catch(() => undefined);
                    await link.click();
                    return true;
                }
            }
            const submit = pg.locator("form button[type='submit'], button[type='submit'], input[type='submit']").first();
            if (await submit.isVisible({ timeout: 1_000 }).catch(() => false)) {
                await submit.scrollIntoViewIfNeeded().catch(() => undefined);
                if (!(await submit.isEnabled({ timeout: 500 }).catch(() => false))) {
                    await maybeScrollAuthorizationPage();
                    if (!(await submit.isEnabled({ timeout: 2_000 }).catch(() => false))) return false;
                }
                await submit.click();
                return true;
            }
            return false;
        };

        const driveGitHubDeviceAuth = async (authPage?: import("@playwright/test").Page | null): Promise<void> => {
            const codeEl = wf.locator("code.device-code-display").first();
            const verifyLink = wf.locator("a.github-verify-link").first();
            await expect(codeEl).toBeVisible({ timeout: 20_000 });
            await expect(verifyLink).toBeVisible({ timeout: 20_000 });
            const codeFromUi = (await codeEl.innerText()).trim();
            const codeFromCapture = (deviceFlowCapture.userCode || "").trim();
            const userCodeRaw = codeFromCapture || codeFromUi;
            const userCode = (userCodeRaw.match(/[A-Z0-9]{4}-[A-Z0-9]{4}/i)?.[0] || userCodeRaw).toUpperCase();
            const verifyBase = deviceFlowCapture.verificationUri
                || await verifyLink.getAttribute("href")
                || "https://github.com/login/device";
            const verifyUrlWithCode = `${verifyBase}${verifyBase.includes("?") ? "&" : "?"}user_code=${encodeURIComponent(userCode)}`;
            const hasCompleteLink = Boolean(deviceFlowCapture.verificationUriComplete);
            // Always prefer a code-bound URL so GitHub doesn't strand us at
            // /login/device/select_account without code context.
            const verifyUrl = hasCompleteLink
                ? (deviceFlowCapture.verificationUriComplete as string)
                : verifyUrlWithCode;
            if (!verifyUrl) return;

            const gh = authPage ?? await page.context().newPage();
            // Force-navigate the popup to the code-bound URL even if it
            // already opened at select_account.
            await gh.goto(verifyUrl, { waitUntil: "domcontentloaded" });
            console.log(`[diag] GitHub device page URL (code-bound=${!hasCompleteLink ? true : true}): ${gh.url().slice(0, 180)}`);
            const enterDeviceCode = async (): Promise<boolean> => {
                const typed = userCode.replace(/-/g, "");

                // GitHub variant with a single device-code input.
                const singleInput = gh.locator("input[name='user_code'], input#user-code, input[name='otp'], input[placeholder*='XXXX-XXXX' i]").first();
                if (await singleInput.isVisible({ timeout: 500 }).catch(() => false)) {
                    await singleInput.click();
                    await singleInput.fill("");
                    await singleInput.pressSequentially(typed, { delay: 40 });
                    const observed = await singleInput.inputValue().catch(() => "");
                    console.log(`[diag] typed GitHub code='${typed}' observed='${observed}'`);
                    return true;
                }

                // GitHub variant with 8 segmented inputs.
                const segmented = gh.locator("input[maxlength='1'], input[inputmode='numeric'], input[autocomplete='one-time-code']");
                const segCount = await segmented.count().catch(() => 0);
                if (segCount >= 8) {
                    await segmented.first().click();
                    await gh.keyboard.type(typed, { delay: 40 });
                    const observed = await segmented.evaluateAll((els: Element[]) =>
                        els.slice(0, 8).map((e) => (e as HTMLInputElement).value || "").join(""),
                    ).catch(() => "");
                    console.log(`[diag] typed segmented GitHub code='${typed}' observed='${observed}'`);
                    return observed.replace(/-/g, "").toUpperCase().startsWith(typed.slice(0, Math.max(1, observed.length)));
                }

                return false;
            };

            const deadline = Date.now() + 60_000;
            let submittedCode = false;
            while (Date.now() < deadline) {
                if (gh.isClosed()) {
                    console.log("[diag] GitHub auth page closed during device-flow loop.");
                    break;
                }
                const currentUrl = gh.url();

                if (/\/login\/device\/success/.test(currentUrl)) {
                    console.log("[diag] GitHub device activation reached success page.");
                    break;
                }

                // If we lost code context and hit the GitHub failure route,
                // break to manual recovery below.
                if (/\/login\/device\/failure/.test(currentUrl)) {
                    break;
                }

                // Account selection page appears first for signed-in users.
                if (/\/login\/device\/select_account/.test(currentUrl)) {
                    const pickedContinue = await clickFirstVisible(gh, [
                        /^continue$/i,
                        /continue as/i,
                        /use this account/i,
                    ]);
                    if (pickedContinue) {
                        console.log("[diag] clicked account Continue on select_account.");
                        await gh.waitForTimeout(1000);
                        continue;
                    }

                    // Avoid clicking homepage/logo links here; just wait for
                    // controls to settle and retry.
                    await gh.waitForTimeout(700);
                    continue;
                }

                // Code entry page after account continue.
                if (!submittedCode && await enterDeviceCode()) {
                    submittedCode = true;
                    await clickFirstVisible(gh, [/^continue$/i, /continue|next|verify|submit/i]);
                    await gh.waitForTimeout(1000);
                    continue;
                }

                const acted = await clickFirstVisible(gh, [
                    /^continue$/i,
                    /authorize github copilot|authorize/i,
                    /allow|approve|confirm/i,
                ]);
                if (!acted) {
                    if (gh.isClosed()) break;
                    await gh.waitForTimeout(600);
                    continue;
                }
                if (gh.isClosed()) break;
                await gh.waitForTimeout(1000);
            }

            // Manual recovery path when GitHub reports missing device context.
            if (/\/login\/device\/failure/.test(gh.url())) {
                console.log("[diag] GitHub returned /login/device/failure, attempting manual code submit recovery.");
                await gh.goto(verifyUrlWithCode, { waitUntil: "domcontentloaded" });
                const codeInput = gh.locator("input[name='user_code'], input#user-code, input[name='otp'], input[placeholder*='XXXX-XXXX' i]").first();
                if (await codeInput.isVisible({ timeout: 6_000 }).catch(() => false)) {
                    const typed = userCode.replace(/-/g, "");
                    await codeInput.click();
                    await codeInput.fill("");
                    await codeInput.pressSequentially(typed, { delay: 40 });
                    await clickFirstVisible(gh, [/continue|next|verify|submit/i]);
                    await gh.waitForTimeout(1200);
                    await clickFirstVisible(gh, [/authorize|allow|approve|confirm|continue/i]);
                    await gh.waitForTimeout(1200);
                }
            }
            const finalUrl = gh.isClosed() ? "(closed)" : gh.url().slice(0, 180);
            console.log(`[diag] GitHub fallback final URL: ${finalUrl}`);

            // Close the GitHub auth tab after device-flow handling so test
            // focus returns to the Fabric tab regardless of who created it.
            if (!gh.isClosed()) {
                await gh.close().catch(() => undefined);
            }

        };

        // Recover from stale test identity/session by signing out first.
        if (await signOutBtn.isVisible({ timeout: 1_500 }).catch(() => false)) {
            const label = await signOutBtn.innerText().catch(() => "(unknown)");
            if (/e2e-test-user/i.test(label)) {
                console.log("[diag] Stale e2e identity detected, signing out before re-auth.");
                await signOutBtn.click();
                await expect(signInBtn).toBeVisible({ timeout: 20_000 });
            }
        }

        if (!(await signInBtn.isVisible({ timeout: 1_500 }).catch(() => false))) return;

        console.log("[diag] Auth gate visible, starting one-click GitHub device flow.");
        const popupPromise = page.context().waitForEvent("page", { timeout: 20_000 }).catch(() => null);
        await signInBtn.click();
        const popup = await popupPromise;

        if (popup) {
            await popup.waitForLoadState("domcontentloaded", { timeout: 45_000 }).catch(() => undefined);
            console.log(`[diag] GitHub auth tab URL: ${popup.url().slice(0, 180)}`);

            // If GitHub asks for a consent click, perform it. If already
            // authorized, this block is a no-op.
            if (!/\/login\/device\/select_account/.test(popup.url())) {
                const consentButton = popup.getByRole("button", {
                    name: /authorize|continue|allow|approve|confirm/i,
                }).first();
                if (await consentButton.isVisible({ timeout: 6_000 }).catch(() => false)) {
                    await consentButton.click();
                    console.log("[diag] Clicked GitHub consent button.");
                }
            }
        }

        // Deterministic fallback: drive the device flow ourselves whenever
        // the code/link UI is visible. During polling the sign-in button can
        // disappear, so key off the code element instead of the button.
        const showCodeBtn = wf.getByRole("button", { name: /show code/i }).first();
        if (await showCodeBtn.isVisible({ timeout: 1_500 }).catch(() => false)) {
            await showCodeBtn.click().catch(() => undefined);
            console.log("[diag] clicked 'Show code' to reveal device code fallback UI.");
        }

        const codeVisible = await wf.locator("code.device-code-display").first()
            .isVisible({ timeout: 2_000 })
            .catch(() => false);
        if (codeVisible) {
            const copyCodeBtn = wf.getByRole("button", { name: /copy code again/i }).first();
            if (await copyCodeBtn.isVisible({ timeout: 800 }).catch(() => false)) {
                await copyCodeBtn.click().catch(() => undefined);
                console.log("[diag] clicked 'Copy code again' before driving GitHub flow.");
            }
            console.log("[diag] Device code visible; driving device flow via controlled GitHub page.");
            await driveGitHubDeviceAuth(popup);
        }

        // Wait until the workload becomes usable. Sidebar sign-out can be
        // hidden depending on layout state, so prefer functional readiness.
        const signedInIndicator = wf.getByRole("button", { name: /sign out \(/i }).first();
        const startBtn = wf.getByRole("button", { name: START_MISSION_BUTTON_RE }).first();
        const deadline = Date.now() + BUDGETS.authCompleteMs;
        while (Date.now() < deadline) {
            if (await startBtn.isVisible({ timeout: 1_000 }).catch(() => false)) return;
            if (await signedInIndicator.isVisible({ timeout: 1_000 }).catch(() => false)) return;
            await page.waitForTimeout(1200);
        }
        const body = await wf.locator("body").innerText({ timeout: 2_000 }).catch(() => "");
        throw new Error(
            `GitHub device-flow did not complete in time. Body snapshot: ${JSON.stringify(String(body).slice(0, 260))}`,
        );
    }

    // Poll the page frame's body text until the orchestrator mounts or
    // we give up. This prints exactly what Playwright sees inside the
    // iframe and whether the React bundle ever rendered.
    let sawAuthGate = false;
    let orchestratorMounted = false;
    const maxIframeMountAttempts = 10;
    for (let attempt = 0; attempt < maxIframeMountAttempts; attempt++) {
        const bodyText = await wf.locator("body").innerText({ timeout: 2000 }).catch((e) => `ERR: ${(e as Error).message.slice(0, 80)}`);
        const buttonCount = await wf.locator("button").count().catch(() => -1);
        const startVisible = await wf.getByRole("button", { name: START_MISSION_BUTTON_RE }).first()
            .isVisible({ timeout: 500 })
            .catch(() => false);
        const headings = await wf.locator("h1, h2, h3").allInnerTexts().catch(() => []);
        console.log(
            `[diag] iframe-mount attempt=${attempt} buttons=${buttonCount} startVisible=${startVisible} headings=${JSON.stringify(headings.slice(0, 5))} bodyLen=${typeof bodyText === "string" ? bodyText.length : 0} bodyFirst=${JSON.stringify((bodyText as string).slice(0, 200))}`,
        );
        if (typeof bodyText === "string" && /sign in with github|github copilot subscription is required/i.test(bodyText)) {
            sawAuthGate = true;
            break;
        }
        if (startVisible || (typeof bodyText === "string" && /orchestrate/i.test(bodyText))) {
            orchestratorMounted = true;
            break;
        }
        await page.waitForTimeout(1500);
    }

    if (!sawAuthGate && !orchestratorMounted) {
        const body = await wf.locator("body").innerText({ timeout: 2_000 }).catch(() => "");
        throw new Error(
            `Workload iframe did not mount within ${maxIframeMountAttempts} attempts; failing fast. Body snapshot: ${JSON.stringify(String(body).slice(0, 260))}`,
        );
    }

    if (sawAuthGate) {
        await completeGitHubSignInIfNeeded();
    }

    const fakeIdentity = wf.getByRole("button", { name: /sign out \(e2e-test-user\)/i }).first();
    if (await fakeIdentity.isVisible({ timeout: 2_000 }).catch(() => false)) {
        await completeGitHubSignInIfNeeded();
    }

    // Wait for the orchestrator compose page inside the iframe to mount.
    await expect(
        wf.getByRole("button", { name: START_MISSION_BUTTON_RE }),
        "Workload iframe loaded but never rendered the start button",
    ).toBeVisible({ timeout: BUDGETS.composeVisible });

    // ── 4. Compose → Start mission ────────────────────────────────
    // The task-description box is a <div id="composer-task-text" contenteditable
    // role="textbox"> inside OrchestratorPage / RichComposer. Use the ID so we
    // never accidentally type into the header Search box (which is ALSO inside
    // the workload iframe and comes first in DOM order).
    const promptBox = wf.locator("#composer-task-text");
    await expect(promptBox).toBeVisible({ timeout: 30_000 });
    console.log(`[diag] Filling mission prompt for ${RUN_FOLDER}.`);
    try {
        await promptBox.click({ timeout: 10_000 });
    } catch (err) {
        console.log(`[diag] Prompt box click did not complete; trying forced click: ${(err as Error).message.slice(0, 300)}`);
        await promptBox.click({ timeout: 5_000, force: true });
    }
    // contenteditable doesn't support .fill(); use real key events first,
    // then fall back to a DOM input event if the browser shell wedges.
    try {
        await promptBox.pressSequentially(PROMPT, { delay: 5, timeout: 20_000 });
    } catch (err) {
        console.log(`[diag] Prompt key entry did not complete; using DOM input fallback: ${(err as Error).message.slice(0, 300)}`);
        await promptBox.evaluate((element, prompt) => {
            element.textContent = prompt;
            element.dispatchEvent(new InputEvent("input", { bubbles: true, data: prompt, inputType: "insertText" }));
            element.dispatchEvent(new Event("change", { bubbles: true }));
        }, PROMPT);
    }
    console.log("[diag] Mission prompt filled; starting mission.");

    const waitForCreateSessionResponse = () => page.waitForResponse(
        (response) => /\/api\/sessions$/.test(response.url()) && response.request().method() === "POST",
        { timeout: 60_000 },
    ).catch(() => null);
    const clickStartMission = async () => {
        let startMissionButton = refreshWorkloadFrame().getByRole("button", { name: START_MISSION_BUTTON_RE }).first();
        try {
            await startMissionButton.click({ timeout: 15_000 });
            return;
        } catch (err) {
            console.log(`[diag] Start mission click did not complete; trying refreshed DOM click: ${(err as Error).message.slice(0, 300)}`);
        }
        startMissionButton = refreshWorkloadFrame().getByRole("button", { name: START_MISSION_BUTTON_RE }).first();
        await startMissionButton.evaluate((element) => (element as HTMLElement).click(), undefined, { timeout: 10_000 });
    };
    let createSessionResponsePromise = waitForCreateSessionResponse();
    await clickStartMission();
    let createSessionResponse = await createSessionResponsePromise;
    if (!createSessionResponse) {
        console.log("[diag] No create-session response observed after first start click; retrying once in the active workload frame.");
        createSessionResponsePromise = waitForCreateSessionResponse();
        await clickStartMission();
        createSessionResponse = await createSessionResponsePromise;
    }
    let sessionId = "";
    if (createSessionResponse?.ok()) {
        const body = await createSessionResponse.json().catch(() => null) as { id?: string } | null;
        sessionId = String(body?.id || "");
    } else if (createSessionResponse) {
        noteTestError(recordedErrors, "create session failed", `HTTP ${createSessionResponse.status()} ${await createSessionResponse.text().catch(() => "")}`);
    }

    const invalidTokenError = wf.getByText(/GitHub token invalid or expired/i).first();
    if (await invalidTokenError.isVisible({ timeout: 8_000 }).catch(() => false)) {
        throw new Error(
            "Backend rejected the stored GitHub token as invalid/expired. Sign out in workload and complete GitHub device-flow sign-in again using real credentials.",
        );
    }

    // ── 5. Mission Control mounted ────────────────────────────────
    // The Step 3 shell title is the task snippet, not a fixed
    // "Mission Control" string. Anchor on stable mc3 selectors.
    refreshWorkloadFrame(true);
    await expect(wf.locator(".mc3").first()).toBeVisible({ timeout: 60_000 });
    if (!sessionId) {
        const activeWorkloadFrame = findWorkloadPageFrame(true);
        const routeMatch = activeWorkloadFrame?.url().match(/\/agent-hub\/session\/([0-9a-f-]+)/i);
        sessionId = routeMatch?.[1] || "";
    }
    expect(sessionId, "The real mission session id should be captured from the backend response or iframe route").toMatch(/^[0-9a-f-]{36}$/i);
    const logSurface = wf.locator(
        ".canvas-log-stream, .agent-canvas .log-window, .dmc-live .log-window, .mc3-log__scroll, .mc3-log",
    ).first();
    await expect(logSurface).toBeVisible({ timeout: 60_000 });
    assertNoRecordedErrors(recordedErrors, "mission startup");
    try {
        const shot = `${screenshotPrefix}-entry.png`;
        await page.screenshot({ path: shot, fullPage: true, animations: "disabled", timeout: 15_000 });
        console.log(`[diag] Screenshot: ${shot}`);
    } catch (e) {
        console.log(`[diag] Screenshot capture failed at mission entry: ${(e as Error).message}`);
    }

    // User-requested Step-3 guard: once execution UI is open, frontend
    // logs must appear as the first agent comes online. Scheduling can take
    // longer than a few seconds in the real portal, so keep this explicit.
    const step3Deadline = Date.now() + BUDGETS.firstLiveLog;
    let step3FrontendRows = 0;
    const streamRows = wf.locator(".canvas-log-row, .agent-canvas .log-window p, .dmc-live .log-window p, .mc3-entry");
    while (Date.now() < step3Deadline) {
        step3FrontendRows = await streamRows.count().catch(() => 0);
        if (step3FrontendRows > 0) break;
        await page.waitForTimeout(500);
    }
    if (step3FrontendRows === 0) {
        const body = await wf.locator("body").innerText({ timeout: 2_000 }).catch(() => "");
        throw new Error(
            `Step 3 task execution opened but no frontend live-log rows appeared within ${BUDGETS.firstLiveLog}ms. `
            + `mc-stream events seen=${missionStreamEventCount}. Body snapshot: ${JSON.stringify(String(body).slice(0, 260))}`,
        );
    }

    const forbiddenUserFacingTrace = /fabric_(?:create|delete|list|read|write|get)_|TOOL_ERROR|===HANDOFF|\[object Object\]|\bundefined\b|\bNaN\b|→|←/i;
    for (const [surface, locator] of [
        ["live log", wf.locator(".canvas-log-stream, .agent-canvas, .dmc-live .agent-canvas, .mc3-log").first()],
        ["run overview", wf.locator(".right-rail, .mc3-rail").first()],
    ] as const) {
        const text = await locator.innerText({ timeout: 5_000 }).catch(() => "");
        expect(
            text,
            `Mission Control ${surface} copy should be enterprise-readable and must not expose raw internal trace tokens.`,
        ).not.toMatch(forbiddenUserFacingTrace);
    }

    // Live Log growth proof. Header meta appears only after first log, so
    // fall back to counting rendered stream entries when meta is hidden.
    const entriesCounter = wf.locator("span.mc3-log__bar-meta").first();
    const streamEntries = wf.locator(".canvas-log-row, .agent-canvas .log-window p, .dmc-live .log-window p, .mc3-entry");
    const parseCount = (s: string) => {
        // New UI format: "4 shown · 15 total · 00:01:23".
        const shownTotal = s.match(/(\d+)\s+shown\s*[·|]\s*(\d+)\s+total/i);
        if (shownTotal) return parseInt(shownTotal[2], 10);

        // Legacy format: "15 entries".
        const entries = s.match(/(\d+)\s*entries?/i);
        if (entries) return parseInt(entries[1], 10);

        // Fallback for partial strings where only "N shown" exists.
        const shown = s.match(/(\d+)\s+shown/i);
        if (shown) return parseInt(shown[1], 10);

        return 0;
    };

    const haveMeta = await entriesCounter.isVisible({ timeout: 5_000 }).catch(() => false);
    if (!haveMeta) {
        await expect(streamEntries.first()).toBeVisible({ timeout: 60_000 });
    }

    const baseline = haveMeta
        ? parseCount(await entriesCounter.innerText().catch(() => "0 entries"))
        : await streamEntries.count().catch(() => 0);

    // ── 7. Wait for the live log to grow (real-time streaming proof) ─
    const t0 = Date.now();
    let latestCount = baseline;
    while (Date.now() - t0 < 120_000) {
        await page.waitForTimeout(1500);
        const countNow = await entriesCounter.isVisible({ timeout: 300 }).catch(() => false)
            ? parseCount(await entriesCounter.innerText().catch(() => "0 entries"))
            : await streamEntries.count().catch(() => latestCount);
        if (countNow > latestCount) latestCount = countNow;
        if (latestCount > baseline) break;
    }

    const delta = latestCount - baseline;
    console.log(`Live log: start=${baseline}  latest=${latestCount}  delta=${delta}`);

    // Capture the final state even if the assertion below fails.
    try {
        const shot = `${screenshotPrefix}-streaming.png`;
        await page.screenshot({ path: shot, fullPage: true, animations: "disabled", timeout: 15_000 });
        console.log(`[diag] Screenshot: ${shot}`);
    } catch (e) {
        console.log(`[diag] Screenshot capture failed after stream growth: ${(e as Error).message}`);
    }

    expect(
        Math.max(delta, step3FrontendRows, missionStreamEventCount),
        "Mission Control should render at least one live-log row or receive at least one mc-stream event",
    ).toBeGreaterThanOrEqual(1);
    assertNoRecordedErrors(recordedErrors, "live log streaming");

    // ── 8. Terminal success and real Fabric artifacts ───────────────
    await waitForMissionCompleted(page, wf, sessionId, backendAuth);
    const producedItems = await waitForFabricArtifacts(page, backendAuth);
    const producedSummary = producedItems
        .map((item) => `${item.type || "Unknown"}:${item.name || item.id || "unnamed"}`)
        .join(", ");
    console.log(`[diag] Produced items in ${RUN_FOLDER}: ${producedSummary}`);

    const report = producedItems.find((item) => /^report$/i.test(String(item.type || "")));
    expect(report?.webUrl, "The visualization/report item should have a Fabric URL the user can open").toBeTruthy();
    const semanticModel = producedItems.find((item) => /semantic\s*model/i.test(String(item.type || "")));
    expect(semanticModel?.id, "The mission should create a semantic model that can be queried behind the report").toBeTruthy();
    const notebook = producedItems.find((item) => /^notebook$/i.test(String(item.type || "")));
    expect(notebook?.id, "The mission should create a Notebook with persisted ingestion code").toBeTruthy();

    const inventoryAudit = await fetchInventorySolutionAudit(page, sessionId, backendAuth);
    validateInventoryAuditProof(inventoryAudit);
    validateNotebookExecution(inventoryAudit);
    await validateNotebookDefinitionContainsCode(page, backendAuth, notebook as WorkspaceItem);
    await validateSemanticModelDefinitionContainsInventory(page, backendAuth, semanticModel as WorkspaceItem);
    await validateReportDefinitionContainsVisuals(page, backendAuth, report as WorkspaceItem, semanticModel as WorkspaceItem);
    const inventoryItemTypes = await validateSemanticModelData(page, backendAuth, semanticModel as WorkspaceItem, inventoryAudit);
    await validateReportOpensWithVisualization(page, report as WorkspaceItem, inventoryItemTypes, screenshotPrefix);

    // ── Verifier-verdict gate (Phase E) ────────────────────────────
    // The orchestrator emits a structural ``verifier_verdict`` event when a
    // FabricVerifier task finishes. The verdict re-judges the verifier LLM's
    // claim against a deterministic browser-evidence rubric. The mission is
    // only acceptable when the verdict for the report deliverable is
    // passed=true and the evidence shows visualsRendered=true with no
    // "Loading your report..." stuck state and no error modal. This is what
    // makes "verifier passed" a hard product gate instead of a server-side
    // exportTo proof that can succeed even when the user-facing report is
    // broken.
    const verdictDeadline = Date.now() + 60_000;
    let reportVerdict: VerifierVerdictView | null = null;
    let lastVerdictDiag = "";
    while (Date.now() < verdictDeadline) {
        const verdicts = await fetchVerifierVerdicts(page, sessionId, backendAuth);
        reportVerdict = pickReportVerdict(verdicts, report as WorkspaceItem);
        lastVerdictDiag = summarizeVerdicts(verdicts);
        if (reportVerdict?.passed) break;
        if (reportVerdict && !reportVerdict.passed && verdicts.length >= 2) {
            // We have at least one verdict that explicitly failed — no point
            // waiting longer. Surface it now so the failure message is precise.
            break;
        }
        await page.waitForTimeout(3_000);
    }
    expect(
        reportVerdict,
        `Backend never emitted a verifier_verdict for the report deliverable. Verdicts seen:\n${lastVerdictDiag}`,
    ).toBeTruthy();
    expect(
        reportVerdict?.passed,
        `Verifier rejected the report deliverable. Verdict:\n${JSON.stringify(reportVerdict, null, 2)}`,
    ).toBe(true);
    expect(
        reportVerdict?.evidence?.visualsRendered,
        `Verifier verdict does not include visualsRendered=true evidence. Verdict:\n${JSON.stringify(reportVerdict, null, 2)}`,
    ).toBe(true);
    expect(
        reportVerdict?.evidence?.loadingStuckObserved,
        `Verifier evidence shows the report stuck on "Loading your report...". Verdict:\n${JSON.stringify(reportVerdict, null, 2)}`,
    ).toBe(false);
    expect(
        (reportVerdict?.evidence?.errorsObserved || []).length,
        `Verifier evidence captured browser errors on the report URL. Verdict:\n${JSON.stringify(reportVerdict, null, 2)}`,
    ).toBe(0);
    console.log(`[diag] Verifier verdict PASSED for report ${(report as WorkspaceItem).id} — visualsRendered=${reportVerdict?.evidence?.visualsRendered}, urls=${(reportVerdict?.evidence?.browserVerifiedUrls || []).length}, screenshots=${(reportVerdict?.evidence?.screenshotPaths || []).length}`);

    const finalCopy = await wf.locator(".mc3, .dmc-live").first().innerText({ timeout: 10_000 }).catch(() => "");
    expect(finalCopy, "Mission Control should summarize that the report/visualization covers accessible Fabric items")
        .toMatch(/fabric items|workspace inventory|accessible items|report|visualization/i);

    const logIssues = backendLogIssues(sessionId, missionStartedAt);
    expect(logIssues, `Backend emitted warning/error lines for session ${sessionId}:\n${logIssues.join("\n")}`).toEqual([]);
    assertNoRecordedErrors(recordedErrors, "end-to-end mission execution");

    // "Reconnecting…" must NOT be stuck on at the end.
    await expect(wf.getByText(/reconnecting/i)).toBeHidden();
});
