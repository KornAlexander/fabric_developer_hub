import { test, expect } from "@playwright/test";

import { runMissionProgressEvidenceTape } from "./utils/missionEvidence";
import { judgeMissionProgressEvidence } from "./utils/llmJudge";
import { resolveGitHubCopilotToken } from "./utils/githubCopilotToken";

const judgeEnabled = process.env.AGENTHUB_E2E_LLM_JUDGE === "1";

test.use({ viewport: { width: 1440, height: 960 } });

test.describe("Mission Control LLM progress judge", () => {
    test.skip(!judgeEnabled, "Set AGENTHUB_E2E_LLM_JUDGE=1 to run the Copilot-backed qualitative progress judge.");

    test("judges staged frontend evidence for user-understandable mission execution", async ({ page, request }, testInfo) => {
        test.setTimeout(180_000);
        const { stages } = await runMissionProgressEvidenceTape(page, testInfo, { screenshots: false });
        const backendUrl = process.env.WORKLOAD_BE_URL || "http://127.0.0.1:5000";
        const githubToken = await resolveGitHubCopilotToken(request, backendUrl);
        testInfo.annotations.push({ type: "llm-judge-token-source", description: githubToken.source });
        const verdict = await judgeMissionProgressEvidence(request, stages, {
            backendUrl,
            githubToken: githubToken.token,
            model: process.env.AGENTHUB_E2E_LLM_JUDGE_MODEL || "gpt-4o-mini",
        });

        await testInfo.attach("llm-judge-verdict.json", {
            body: JSON.stringify(verdict, null, 2),
            contentType: "application/json",
        });

        if (process.env.AGENTHUB_E2E_LLM_JUDGE_SOFT === "1") {
            testInfo.annotations.push({
                type: verdict.pass ? "llm-judge-pass" : "llm-judge-soft-fail",
                description: JSON.stringify(verdict.blockingIssues),
            });
            return;
        }

        expect(verdict.blockingIssues, JSON.stringify(verdict, null, 2)).toEqual([]);
        expect(verdict.pass, JSON.stringify(verdict, null, 2)).toBe(true);
    });
});