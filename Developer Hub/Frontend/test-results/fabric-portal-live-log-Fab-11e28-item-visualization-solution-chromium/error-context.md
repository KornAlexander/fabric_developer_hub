# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: fabric-portal-live-log.spec.ts >> Fabric portal: Developer Hub creates a real Fabric item visualization solution
- Location: e2e/fabric-portal-live-log.spec.ts:666:5

# Error details

```
Error: Backend never emitted a verifier_verdict for the report deliverable. Verdicts seen:
(none)

expect(received).toBeTruthy()

Received: null
```

```
Error: apiRequestContext._wrapApiCall: ENOENT: no such file or directory, open '/home/lukaszobst/Fabric ClawHub/Developer Hub/Frontend/test-results/.playwright-artifacts-0/traces/77d6c0bc9bfcc94ff8ff-044f81773f5d3766e658.network'
```

# Test source

```ts
  1  | import { test as base, expect, chromium, type BrowserContext } from "@playwright/test";
  2  | import fs from "node:fs";
  3  | import path from "node:path";
  4  | 
  5  | /**
  6  |  * Shared Playwright fixtures.
  7  |  *
  8  |  * Set ``PLAYWRIGHT_USER_DATA_DIR`` to a Chromium user-data directory to
  9  |  * reuse a real browser profile (cookies, logged-in sessions, etc.) for
  10 |  * the test run. If unset, tests use Playwright's default ephemeral
  11 |  * context — existing behaviour.
  12 |  *
  13 |  * Workflow for authenticated tests:
  14 |  *   1.  ``npm run login:e2e``   # opens the shared profile; log in; close
  15 |  *   2.  ``PLAYWRIGHT_USER_DATA_DIR=$HOME/.config/chromium-wsl npm run test:e2e``
  16 |  *
  17 |  * IMPORTANT: only one Chromium process can use a user-data-dir at a
  18 |  * time. Close the login window before starting the test run.
  19 |  */
  20 | 
  21 | const userDataDir = process.env.PLAYWRIGHT_USER_DATA_DIR;
  22 | 
  23 | export const test = base.extend<{ context: BrowserContext }>({
  24 |     context: async ({ browser, browserName, headless }, use) => {
  25 |         if (!userDataDir || browserName !== "chromium") {
  26 |             // Default path: let Playwright build a fresh context per test.
  27 |             const ctx = await browser.newContext();
  28 |             await use(ctx);
  29 |             await ctx.close();
  30 |             return;
  31 |         }
  32 | 
  33 |         fs.mkdirSync(userDataDir, { recursive: true });
  34 |         const lock = path.join(userDataDir, "SingletonLock");
  35 |         if (fs.existsSync(lock)) {
  36 |             throw new Error(
  37 |                 `Chromium profile ${userDataDir} is in use (SingletonLock present). ` +
  38 |                     `Close the browser window opened via "npm run login:e2e" before running tests.`,
  39 |             );
  40 |         }
  41 | 
  42 |         // Honor Playwright's resolved ``headless`` (driven by --headed CLI
  43 |         // flag, VS Code "Show browser" toggle, or ``use.headless`` in the
  44 |         // config). ``PLAYWRIGHT_HEADFUL=1`` is an additional escape hatch
  45 |         // for shells that can't pass ``--headed``.
  46 |         const runHeadless = process.env.PLAYWRIGHT_HEADFUL ? false : headless;
  47 |         const ctx = await chromium.launchPersistentContext(userDataDir, {
  48 |             headless: runHeadless,
  49 |             args: [
  50 |                 "--no-sandbox",
  51 |                 // Chromium's Private Network Access (PNA) blocks loopback
  52 |                 // fetches from app.powerbi.com with "Permission was denied
  53 |                 // for this request to access the `loopback` address space",
  54 |                 // even when the loopback dev-server sends
  55 |                 // Access-Control-Allow-Private-Network: true.  Disabling
  56 |                 // just these features is targeted enough NOT to break
  57 |                 // OAuth/CSRF protections that --disable-web-security would.
  58 |                 // Also disable site isolation so Playwright can inspect the
  59 |                 // cross-origin (app.powerbi.com → 127.0.0.1) workload
  60 |                 // iframe — without this the orchestrator frame appears in
  61 |                 // page.frames() but all getByRole queries time out.
  62 |                 "--disable-features=BlockInsecurePrivateNetworkRequests,PrivateNetworkAccessRespectPreflightResults,PrivateNetworkAccessSendPreflights,LocalNetworkAccessChecks,PrivateNetworkAccessPermissionPrompt,IsolateOrigins,site-per-process,SitePerProcess,ProcessPerSiteUpToMainFrameThreshold",
  63 |                 "--disable-site-isolation-trials",
  64 |             ],
  65 |         });
  66 |         await use(ctx);
> 67 |         await ctx.close();
     |                   ^ Error: apiRequestContext._wrapApiCall: ENOENT: no such file or directory, open '/home/lukaszobst/Fabric ClawHub/Developer Hub/Frontend/test-results/.playwright-artifacts-0/traces/77d6c0bc9bfcc94ff8ff-044f81773f5d3766e658.network'
  68 |     },
  69 | });
  70 | 
  71 | export { expect };
  72 | 
```