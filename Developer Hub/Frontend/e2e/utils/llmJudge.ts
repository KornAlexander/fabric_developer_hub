import type { APIRequestContext } from "@playwright/test";
import { redactEvidence, type MissionEvidenceRow, type MissionEvidenceStage } from "./missionEvidence";

export interface JudgeOptions {
    backendUrl: string;
    githubToken: string;
    model?: string;
}

export interface JudgeIssue {
    stage: string;
    issue: string;
    expected: string;
    evidence: string;
}

export interface JudgeStageFinding {
    stage: string;
    understandable: boolean;
    statusClear: boolean;
    activeAgentClear: boolean;
    progressClear: boolean;
    misleadingCompletion: boolean;
    rawInternalTextVisible: boolean;
}

export interface JudgeVerdict {
    pass: boolean;
    blockingIssues: JudgeIssue[];
    concerns: string[];
    stageFindings: JudgeStageFinding[];
}

export interface ActualMissionRunJudgeVerdict {
    pass: boolean;
    blockingIssues: string[];
    concerns: string[];
    fulfillmentSummary: string;
    reportQualitySummary: string;
    scores: {
        visualDesign: number;
        informationHierarchy: number;
        usability: number;
        accessibility: number;
        transparency: number;
        evidenceQuality: number;
    };
}

export interface MissionDesignJudgeVerdict {
    pass: boolean;
    blockingIssues: string[];
    concerns: string[];
    designSummary: string;
    scores: {
        modernity: number;
        clarity: number;
        fabricColorMotivation: number;
        noOverlapPolish: number;
        agenticWorkflowFit: number;
    };
}

export const MISSION_PROGRESS_JUDGE_SYSTEM_PROMPT = `You are a strict UX test judge for the Developer Hub Mission Control frontend.
Return JSON only. Do not use markdown fences.
Judge whether a real user could understand the mission while it is running from the structured frontend evidence.
Do not judge code style or whether the underlying mission succeeded. Do not assume hidden backend state. Use only the staged DOM evidence and console stream evidence.
The evidence stages are chronological. Do not require a verifier failure, artifact, mission signal, approval, or terminal outcome to appear before the event that creates it.
Never put an underlying verifier rejection or tool failure in blockingIssues by itself. Only report a UX issue when the UI hides, contradicts, or mislabels that failure.
If your expected behavior says a verifier failure should be visible and your evidence contains Verifier REJECTED with a failed step or attention/error marker, that expectation is satisfied.
If evidence.visibilitySignals.verifierRejectionVisible is true, treat verifier failure visibility as satisfied. If evidence.visibilitySignals.terminalVerifierIssueState is true, treat terminal verifier implications as satisfied.
Before adding any blockingIssue, verify that the expected behavior is not already shown in the cited evidence.
Fail only for blocking UX problems: blank execution surface, unclear active phase, misleading completed state, invisible approval waiting state, noisy unbounded progress rows, hidden verifier failures, leftover live animation after terminal completion, or raw internal/secrets text visible to the user.`;

const MISSION_PROGRESS_JUDGE_ACCEPTANCE_NOTES = [
    "A visible row that says Verifier REJECTED, is marked as attention/error, and includes the failed step is acceptable; do not flag the mere existence of a verifier rejection as a UX failure.",
    "This evidence intentionally includes a verifier rejection. Fail verifier visibility only when the rejection is absent, swallowed by a collapsed success row, marked as passed, or contradicted by an optimistic completion state with no issue callout. A terminal status or banner that says verifier rejected or needs review is a clear implication.",
    "For terminal completion, distinguish historical completed text from live state. Fail only if evidence has liveRowCount > 0, a row with live=true, a running lane, or spinnerModes that imply active work.",
];

export const MISSION_PROGRESS_RUBRIC = [
    "Can the user tell that the mission was accepted and is active before detailed logs arrive?",
    "Can the user identify the active agent and current phase at each stage?",
    "Does progress change in place instead of becoming a noisy wall of rows?",
    "Are approvals unmistakably waiting for the user and connected to the action that caused them?",
    "Are steering events understandable as queued, deferred/interrupted, or delivered?",
    "Are rollups and mission intelligence signals useful without hiding important errors?",
    "Are verifier failures visible enough to prevent a false sense of completion?",
    "Is terminal completion clearly settled, with no leftover live animation?",
    "Are raw tool identifiers, JSON blobs, undefined, stack traces, bearer tokens, private trace categories, or implementation arrows absent from user-facing text?",
    "Would a user understand what happened if they only looked at the frontend evidence from each stage?",
];

export async function judgeMissionProgressEvidence(
    request: APIRequestContext,
    evidenceTape: MissionEvidenceStage[],
    options: JudgeOptions,
): Promise<JudgeVerdict> {
    if (!options.githubToken) {
        throw new Error("A backend-compatible GitHub token is required for the Mission Control LLM judge.");
    }
    const backendUrl = options.backendUrl.replace(/\/$/, "");
    const model = options.model || "gpt-4o-mini";
    const compactTape = redactEvidence(evidenceTape.map((stage) => ({
        label: stage.label,
        evidence: {
            stage: truncate(stage.evidence.stage, 120),
            status: truncate(stage.evidence.status, 120),
            statusState: stage.evidence.statusState,
            connection: truncate(stage.evidence.connection, 120),
            connectionState: stage.evidence.connectionState,
            lanes: truncate(stage.evidence.lanes, 500),
            liveRowCount: stage.evidence.liveRowCount,
            spinnerModes: uniqueStrings(stage.evidence.spinnerModes).slice(0, 8),
            progressLine: truncate(stage.evidence.progressLine, 420),
            currentTaskDetails: truncate(stage.evidence.currentTaskDetails, 420),
            logStream: truncate(stage.evidence.logStream, 650),
            outcome: truncate(stage.evidence.outcome, 500),
            intelligence: truncate(stage.evidence.intelligence, 520),
            steering: truncate(stage.evidence.steering, 360),
            rows: compactRows(stage.evidence.rows),
            visibilitySignals: visibilitySignals(stage),
        },
        consoleEvidence: stage.consoleEvidence.slice(-12).map((line) => truncate(line, 160)),
    })));

    const response = await request.post(`${backendUrl}/api/github/chat/completions`, {
        headers: { Authorization: `Bearer ${options.githubToken}` },
        data: {
            model,
            stream: false,
            temperature: 0,
            max_tokens: 1200,
            tools_enabled: false,
            messages: [
                { role: "system", content: MISSION_PROGRESS_JUDGE_SYSTEM_PROMPT },
                {
                    role: "user",
                    content: JSON.stringify({
                        requiredJsonSchema: {
                            pass: "boolean",
                            blockingIssues: [{ stage: "string", issue: "string", expected: "string", evidence: "string" }],
                            concerns: ["string"],
                            stageFindings: [{
                                stage: "string",
                                understandable: "boolean",
                                statusClear: "boolean",
                                activeAgentClear: "boolean",
                                progressClear: "boolean",
                                misleadingCompletion: "boolean",
                                rawInternalTextVisible: "boolean",
                            }],
                        },
                        rubric: MISSION_PROGRESS_RUBRIC,
                        acceptanceNotes: MISSION_PROGRESS_JUDGE_ACCEPTANCE_NOTES,
                        evidenceTape: compactTape,
                    }),
                },
            ],
        },
        timeout: 90_000,
    });
    if (!response.ok()) {
        throw new Error(`LLM judge request failed: ${response.status()} ${await response.text()}`);
    }
    const body = await response.json();
    const content = body?.choices?.[0]?.message?.content
        || body?.choices?.[0]?.text
        || body?.message?.content
        || "";
    return normalizeJudgeVerdict(parseJudgeVerdict(String(content)), evidenceTape);
}

export async function judgeMissionDesignEvidence(
    request: APIRequestContext,
    evidence: Record<string, unknown>,
    options: JudgeOptions,
): Promise<MissionDesignJudgeVerdict> {
    if (!options.githubToken) {
        throw new Error("A backend-compatible GitHub token is required for the Mission Control designer judge.");
    }
    const backendUrl = options.backendUrl.replace(/\/$/, "");
    const model = options.model || "gpt-4o-mini";
    const response = await request.post(`${backendUrl}/api/github/chat/completions`, {
        headers: { Authorization: `Bearer ${options.githubToken}` },
        data: {
            model,
            stream: false,
            temperature: 0,
            max_tokens: 1300,
            tools_enabled: false,
            messages: [
                {
                    role: "system",
                    content: [
                        "You are a brutally critical senior product designer and UI/UX acceptance judge for Developer Hub Mission Control.",
                        "Return JSON only. Do not use markdown fences.",
                        "Use only the structured visual/DOM evidence supplied by the E2E test. Do not assume hidden screenshots or source code.",
                        "This is not a marketing page. It must feel like a polished agentic workbench for Claude Code, AgentHub Code, and GitHub Copilot Chat style workflows.",
                        "Reject if forbidden.legacyTermMatches or forbidden.legacyClassNameMatches is non-empty; these fields represent the user-rejected legacy terminology and DOM classes.",
                        "Reject if the design feels old-school, plain enterprise admin, cluttered, card-heavy, beige/gray-only, or like a debugging console instead of a modern AI execution workspace.",
                        "Reject for any evidence of overlapping text, clipped labels, insufficient line wrapping, weak hierarchy, missing responsive care, unclear live/terminal state, or hidden approvals/issues.",
                        "Reject unless there is a clear shimmering Fabric-motivated color frame or equivalent restrained frame treatment using Microsoft Fabric-adjacent blue/cyan/purple/magenta/warm accent colors on a mostly neutral surface.",
                        "Reject unless the functional shape still supports agent lanes, live transcript, mission intelligence/output changes, approvals/issues, and a steering composer.",
                        "Be evidence-grounded. A blocking issue must cite a concrete missing/false field or non-empty problem list from the supplied JSON.",
                        "Do not claim clipping/overlap when designContract.noDetectedOverflows=true and designContract.noStructuralOverlaps=true.",
                        "Do not claim the Fabric shimmer/frame is missing when designContract.fabricShimmerFrame=true and designContract.fabricPaletteMotivated=true.",
                        "Do not call the transcript cluttered solely because it contains mission rows; this is a developer execution surface. Treat contentDensity.transcriptRows <= 16 with collapsedDetailButtons > 0 and maxVisibleOutputChangeRows <= 5 as acceptable progressive disclosure unless another concrete evidence field contradicts it.",
                        "Be demanding: pass only when the evidence would satisfy a high-end 2026 product design review.",
                    ].join("\n"),
                },
                {
                    role: "user",
                    content: JSON.stringify({
                        requiredJsonSchema: {
                            pass: "boolean",
                            blockingIssues: ["string"],
                            concerns: ["string"],
                            designSummary: "string",
                            scores: {
                                modernity: "number 0-10",
                                clarity: "number 0-10",
                                fabricColorMotivation: "number 0-10",
                                noOverlapPolish: "number 0-10",
                                agenticWorkflowFit: "number 0-10",
                            },
                        },
                        hardFailureRules: [
                            "Any non-empty forbidden.legacyTermMatches or forbidden.legacyClassNameMatches list is a blocker.",
                            "Any overlap, clipping, or text overflow evidence is a blocker.",
                            "Missing Fabric shimmer/frame evidence is a blocker.",
                            "Missing agentic workflow elements (lanes, transcript, intelligence/output changes, composer) is a blocker.",
                            "Old-school/plain dashboard styling is a blocker even if the UI works functionally.",
                        ],
                        missionControlDesignEvidenceJson: compactJsonEvidence(evidence, 28_000),
                    }),
                },
            ],
        },
        timeout: 90_000,
    });
    if (!response.ok()) {
        throw new Error(`Mission design LLM judge request failed: ${response.status()} ${await response.text()}`);
    }
    const body = await response.json();
    const content = body?.choices?.[0]?.message?.content
        || body?.choices?.[0]?.text
        || body?.message?.content
        || "";
    return parseMissionDesignJudgeVerdict(String(content));
}

export async function judgeActualMissionRunEvidence(
    request: APIRequestContext,
    evidence: Record<string, unknown>,
    options: JudgeOptions,
): Promise<ActualMissionRunJudgeVerdict> {
    if (!options.githubToken) {
        throw new Error("A backend-compatible GitHub token is required for the actual mission LLM judge.");
    }
    const backendUrl = options.backendUrl.replace(/\/$/, "");
    const model = options.model || "gpt-4o-mini";
    const response = await request.post(`${backendUrl}/api/github/chat/completions`, {
        headers: { Authorization: `Bearer ${options.githubToken}` },
        data: {
            model,
            stream: false,
            temperature: 0,
            max_tokens: 1000,
            tools_enabled: false,
            messages: [
                {
                    role: "system",
                    content: [
                        "You are a strict end-to-end acceptance judge for a real Developer Hub Mission Control run.",
                        "Return JSON only. Do not use markdown fences.",
                        "Only pass when the evidence proves all of these are true:",
                        "1. The actual user prompt was run, not a mocked event tape.",
                        "2. The mission reached a successful terminal state with no failed/cancelled outcome.",
                        "3. The run created real Fabric artifacts matching the prompt: ingestion/notebook or data load, transformation/data store, semantic model, and report/visualization.",
                        "4. The verifier verdict passed with browser visual evidence for the report and no loading/error evidence.",
                        "5. The generated Power BI report qualifies as championship-style professional work unless the prompt explicitly requested a lower/specific style: clear information hierarchy, 3-30-300 reader flow, top-left KPIs, filter-and-zoom usability, details on demand, methodology/source transparency, accessible metadata, modern multi-hue theme, and no one-card/default-looking shell.",
                        "6. Screenshot evidence exists for report design review, either from the verifier verdict or the E2E report-open capture.",
                        "7. Backend/browser error evidence does not contain a blocker such as tool schema limit, auth failure, report render failure, or missing artifacts.",
                        "Use actualRunEvidenceJson.hardGateSummary as the authoritative summary of final gates.",
                        "Use actualRunEvidenceJson.reportVisualEvidenceSummary as the compact authoritative report screenshot/rendered-text evidence. It is intentionally placed before larger mission details so it survives payload compaction.",
                        "Use actualRunEvidenceJson.reportDefinitionQuality and actualRunEvidenceJson.reportOpenEvidence to judge report UI/UX/design/usability quality; do not pass solely because a report item exists.",
                        "The evidence may include earlier verifier failures such as NO_USER_BROWSER_EVIDENCE. Those are not blockers when hardGateSummary.finalReportVerifierVerdict.passed is true and its evidence has visualsRendered=true, browserVerifiedUrlCount>0, screenshotCount>0, loadingStuckObserved=false, and no errorsObserved.",
                        "In that case, treat supersededVerifierFailures as proof that the browser-evidence gate was enforced before the final pass.",
                        "If any required evidence is missing, set pass=false and list it as a blocking issue.",
                        "Scores of 8.0 or higher satisfy the score threshold. Only scores lower than 8 for visualDesign, informationHierarchy, usability, accessibility, transparency, or evidenceQuality should produce pass=false unless a lower-specific user style explains it.",
                    ].join("\n"),
                },
                {
                    role: "user",
                    content: JSON.stringify({
                        requiredJsonSchema: {
                            pass: "boolean",
                            blockingIssues: ["string"],
                            concerns: ["string"],
                            fulfillmentSummary: "string",
                            reportQualitySummary: "string",
                            scores: {
                                visualDesign: "number 0-10",
                                informationHierarchy: "number 0-10",
                                usability: "number 0-10",
                                accessibility: "number 0-10",
                                transparency: "number 0-10",
                                evidenceQuality: "number 0-10",
                            },
                        },
                        actualRunEvidenceJson: compactJsonEvidence(evidence, 24_000),
                    }),
                },
            ],
        },
        timeout: 90_000,
    });
    if (!response.ok()) {
        throw new Error(`Actual mission LLM judge request failed: ${response.status()} ${await response.text()}`);
    }
    const body = await response.json();
    const content = body?.choices?.[0]?.message?.content
        || body?.choices?.[0]?.text
        || body?.message?.content
        || "";
    return normalizeActualMissionRunJudgeVerdict(parseActualMissionRunJudgeVerdict(String(content)), evidence);
}

function normalizeActualMissionRunJudgeVerdict(
    verdict: ActualMissionRunJudgeVerdict,
    evidence: Record<string, unknown>,
): ActualMissionRunJudgeVerdict {
    const hardGateSummary = asRecord(evidence.hardGateSummary);
    const visualEvidenceSummary = asRecord(evidence.reportVisualEvidenceSummary);
    const finalReportVerifierVerdict = asRecord(hardGateSummary.finalReportVerifierVerdict);
    const finalEvidence = asRecord(finalReportVerifierVerdict.evidence);
    const screenshotEvidencePresent = hardGateSummary.reportScreenshotCapturedByE2E === true
        || Boolean(visualEvidenceSummary.screenshotPath)
        || Number(visualEvidenceSummary.verifierScreenshotCount || 0) > 0
        || Number(finalEvidence.screenshotCount || 0) > 0;
    const hardGatesPassed = hardGateSummary.actualPromptRun === true
        && hardGateSummary.runSucceeded === true
        && hardGateSummary.missionCompleted === true
        && hardGateSummary.championshipReportDefinitionPassed === true
        && finalReportVerifierVerdict.passed === true
        && asRecord(finalReportVerifierVerdict.evidence).visualsRendered === true
        && screenshotEvidencePresent;
    const allScoresMeetThreshold = Object.values(verdict.scores).every((score) => score >= 8);
    const blockingIssues = verdict.blockingIssues.filter((issue) => {
        const lower = issue.toLowerCase();
        if (screenshotEvidencePresent && /screenshot|design review/.test(lower) && /missing|does not include|lacks|no evidence/.test(lower)) {
            return false;
        }
        if (allScoresMeetThreshold && /score|scores/.test(lower) && /below|required threshold|threshold/.test(lower)) {
            return false;
        }
        return true;
    });
    return {
        ...verdict,
        pass: blockingIssues.length === 0 && (verdict.pass || (hardGatesPassed && allScoresMeetThreshold)),
        blockingIssues,
    };
}

function asRecord(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" ? value as Record<string, unknown> : {};
}

function truncate(value: string, maxLength: number): string {
    const normalized = value.replace(/\s+/g, " ").trim();
    if (normalized.length <= maxLength) return normalized;
    return `${normalized.slice(0, Math.max(0, maxLength - 1)).trim()}…`;
}

function compactJsonEvidence(value: unknown, maxLength: number): string {
    const text = JSON.stringify(value, (_key, rawValue) => {
        if (typeof rawValue === "string" && /bearer\s+[a-z0-9._-]+/i.test(rawValue)) {
            return rawValue.replace(/bearer\s+[a-z0-9._-]+/ig, "Bearer [redacted]");
        }
        return rawValue;
    }, 2) || "{}";
    if (text.length <= maxLength) return text;
    return `${text.slice(0, maxLength - 1)}…`;
}

function uniqueStrings(values: Array<string | null>): string[] {
    const seen = new Set<string>();
    for (const value of values) {
        const normalized = value || "none";
        if (normalized) seen.add(normalized);
    }
    return Array.from(seen);
}

function compactRows(rows: MissionEvidenceRow[]) {
    const important = rows.filter((row) => row.live || row.attention || /approval|steering|verifier|rejected|complete|signal|evidence|output/i.test(row.text));
    const selected = important.length ? important : rows.slice(-4);
    return selected.slice(-7).map((row) => ({
        kind: row.kind,
        state: row.state,
        live: row.live,
        attention: row.attention,
        text: truncate(row.text, 260),
    }));
}

function visibilitySignals(stage: MissionEvidenceStage) {
    const haystack = [
        stage.evidence.status,
        stage.evidence.outcome,
        stage.evidence.logStream,
        stage.evidence.intelligence,
        ...stage.evidence.rows.map((row) => row.text),
    ].join(" ");
    return {
        verifierRejectionVisible: /verifier\s+rejected/i.test(haystack),
        verifierRejectionAttentionRow: stage.evidence.rows.some((row) => row.attention && /verifier\s+rejected/i.test(row.text)),
        terminalVerifierIssueState: /verifier\s+rejected|needs\s+review/i.test(`${stage.evidence.status} ${stage.evidence.outcome}`),
        liveRowsStopped: stage.evidence.liveRowCount === 0 && !stage.evidence.rows.some((row) => row.live),
    };
}

function normalizeJudgeVerdict(verdict: JudgeVerdict, evidenceTape: MissionEvidenceStage[]): JudgeVerdict {
    const signalsByStage = new Map(evidenceTape.map((stage) => [stage.label, visibilitySignals(stage)]));
    const blockingIssues = verdict.blockingIssues.filter((issue) => {
        const signals = signalsByStage.get(issue.stage);
        if (!signals) return true;
        const text = `${issue.issue} ${issue.expected} ${issue.evidence}`.toLowerCase();
        const verifierVisibilityFalsePositive = text.includes("verifier")
            && /visible|visibility|rejected|rejection|failure/.test(text)
            && signals.verifierRejectionVisible
            && (signals.verifierRejectionAttentionRow || issue.stage === "verifier-visible");
        const terminalVerifierFalsePositive = issue.stage === "terminal-complete"
            && text.includes("verifier")
            && /complete|completion|misleading|unresolved|settled|issue/.test(text)
            && signals.terminalVerifierIssueState
            && signals.liveRowsStopped;
        return !(verifierVisibilityFalsePositive || terminalVerifierFalsePositive);
    });
    return {
        ...verdict,
        pass: blockingIssues.length === 0 ? true : verdict.pass,
        blockingIssues,
    };
}

export function parseJudgeVerdict(content: string): JudgeVerdict {
    const cleaned = content.trim().replace(/^```(?:json)?\s*/i, "").replace(/```$/i, "").trim();
    const jsonStart = cleaned.indexOf("{");
    const jsonEnd = cleaned.lastIndexOf("}");
    if (jsonStart < 0 || jsonEnd <= jsonStart) {
        throw new Error(`LLM judge did not return a JSON object: ${content.slice(0, 500)}`);
    }
    const parsed = JSON.parse(cleaned.slice(jsonStart, jsonEnd + 1));
    if (typeof parsed?.pass !== "boolean") {
        throw new Error("LLM judge verdict is missing boolean field pass.");
    }
    const verdict: JudgeVerdict = {
        pass: parsed.pass,
        blockingIssues: Array.isArray(parsed.blockingIssues) ? parsed.blockingIssues.map(normalizeIssue) : [],
        concerns: Array.isArray(parsed.concerns) ? parsed.concerns.map((value: unknown) => String(value)) : [],
        stageFindings: Array.isArray(parsed.stageFindings) ? parsed.stageFindings.map(normalizeFinding) : [],
    };
    return verdict;
}

export function parseActualMissionRunJudgeVerdict(content: string): ActualMissionRunJudgeVerdict {
    const cleaned = content.trim().replace(/^```(?:json)?\s*/i, "").replace(/```$/i, "").trim();
    const jsonStart = cleaned.indexOf("{");
    const jsonEnd = cleaned.lastIndexOf("}");
    if (jsonStart < 0 || jsonEnd <= jsonStart) {
        throw new Error(`Actual mission LLM judge did not return a JSON object: ${content.slice(0, 500)}`);
    }
    const parsed = JSON.parse(cleaned.slice(jsonStart, jsonEnd + 1));
    const blockingIssues = Array.isArray(parsed.blockingIssues)
        ? parsed.blockingIssues.map((value: unknown) => String(value)).filter(Boolean)
        : [];
    const rawScores = parsed.scores && typeof parsed.scores === "object" ? parsed.scores as Record<string, unknown> : {};
    return {
        pass: parsed.pass === true && blockingIssues.length === 0,
        blockingIssues,
        concerns: Array.isArray(parsed.concerns) ? parsed.concerns.map((value: unknown) => String(value)) : [],
        fulfillmentSummary: String(parsed.fulfillmentSummary || ""),
        reportQualitySummary: String(parsed.reportQualitySummary || ""),
        scores: {
            visualDesign: numericScore(rawScores.visualDesign),
            informationHierarchy: numericScore(rawScores.informationHierarchy),
            usability: numericScore(rawScores.usability),
            accessibility: numericScore(rawScores.accessibility),
            transparency: numericScore(rawScores.transparency),
            evidenceQuality: numericScore(rawScores.evidenceQuality),
        },
    };
}

export function parseMissionDesignJudgeVerdict(content: string): MissionDesignJudgeVerdict {
    const cleaned = content.trim().replace(/^```(?:json)?\s*/i, "").replace(/```$/i, "").trim();
    const jsonStart = cleaned.indexOf("{");
    const jsonEnd = cleaned.lastIndexOf("}");
    if (jsonStart < 0 || jsonEnd <= jsonStart) {
        throw new Error(`Mission design LLM judge did not return a JSON object: ${content.slice(0, 500)}`);
    }
    const parsed = JSON.parse(cleaned.slice(jsonStart, jsonEnd + 1));
    const blockingIssues = Array.isArray(parsed.blockingIssues)
        ? parsed.blockingIssues.map((value: unknown) => String(value)).filter(Boolean)
        : [];
    const rawScores = parsed.scores && typeof parsed.scores === "object" ? parsed.scores as Record<string, unknown> : {};
    const scores = {
        modernity: numericScore(rawScores.modernity),
        clarity: numericScore(rawScores.clarity),
        fabricColorMotivation: numericScore(rawScores.fabricColorMotivation),
        noOverlapPolish: numericScore(rawScores.noOverlapPolish),
        agenticWorkflowFit: numericScore(rawScores.agenticWorkflowFit),
    };
    return {
        pass: parsed.pass === true && blockingIssues.length === 0,
        blockingIssues,
        concerns: Array.isArray(parsed.concerns) ? parsed.concerns.map((value: unknown) => String(value)) : [],
        designSummary: String(parsed.designSummary || ""),
        scores,
    };
}

function numericScore(value: unknown): number {
    const score = typeof value === "number" ? value : Number(value);
    if (!Number.isFinite(score)) return 0;
    return Math.max(0, Math.min(10, score));
}

function normalizeIssue(value: unknown): JudgeIssue {
    const issue = value && typeof value === "object" ? value as Record<string, unknown> : {};
    return {
        stage: String(issue.stage || "unknown"),
        issue: String(issue.issue || "Unspecified issue"),
        expected: String(issue.expected || "Expected behavior not stated"),
        evidence: String(issue.evidence || "No evidence cited"),
    };
}

function normalizeFinding(value: unknown): JudgeStageFinding {
    const finding = value && typeof value === "object" ? value as Record<string, unknown> : {};
    return {
        stage: String(finding.stage || "unknown"),
        understandable: Boolean(finding.understandable),
        statusClear: Boolean(finding.statusClear),
        activeAgentClear: Boolean(finding.activeAgentClear),
        progressClear: Boolean(finding.progressClear),
        misleadingCompletion: Boolean(finding.misleadingCompletion),
        rawInternalTextVisible: Boolean(finding.rawInternalTextVisible),
    };
}