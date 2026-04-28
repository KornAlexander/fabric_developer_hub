# Opening external tabs from inside the Fabric iframe

## The problem

Workloads run inside Fabric's cross-origin iframe. That iframe is
sandboxed, which means the browser blocks:

- `window.open(url, "_blank", ...)` — silently no-ops or returns `null`.
- `<a href="..." target="_blank">` — click-through is blocked unless the
  host grants popups.
- `window.confirm` / `window.alert` — blocked too, which is why we
  render an in-app confirm dialog for the session-cancel flow.

Every attempt at a user-gesture-based popup hits the same wall: the
sandbox policy strips the popup capability, and the gesture never
reaches the underlying browser.

## The workaround — `workloadClient.navigation.openBrowserTab`

The Fabric Workload Client SDK exposes a navigation API that runs in
the **host portal**, not inside the iframe. Calls are proxied out of
the sandbox via `postMessage` to the portal, which then performs the
navigation on behalf of the iframe. The portal is a regular
first-party page and can open tabs freely.

```ts
import type { WorkloadClientAPI } from "@ms-fabric/workload-client";

async function openExternalTab(
    workloadClient: WorkloadClientAPI,
    url: string,
) {
    await workloadClient.navigation.openBrowserTab({
        url,
        queryParams: {},
    });
}
```

Key properties:

1. **No popup blocker involvement.** The browser sees the host portal
   opening the tab; the sandbox rules that apply to the iframe don't
   apply to the portal.
2. **Must still be in a user gesture.** The host portal relays the
   call synchronously enough that Chromium treats it as user-activated.
   If you call it from a timer or a fetch-chain that already
   `await`-ed, the activation is usually lost and the tab won't open.
   Kick it off from the click handler, before the first `await` if you
   can.
3. **Absolute URLs only.** The SDK rejects relative paths. For
   app-internal deep links we build a full `https://…` URL via
   `workloadClient.navigation.navigate("host", { path: … })` or the
   helper in
   [Frontend/src/controller/AgentHubApi.ts](Developer%20Hub/Frontend/src/controller/AgentHubApi.ts#L564)
   .
4. **`queryParams` is required.** Pass `{}` if you have nothing to
   append — omitting the field makes older SDK versions throw.
5. **Error-handling is on you.** The promise rejects if the portal
   refuses (e.g. running outside Fabric, or a malformed URL). Always
   wrap in `try/catch` and provide a fallback UI (a link the user can
   copy, or the original in-page flow).

## Wrapper in this repo

The project wraps the SDK call in
[callNavigationOpenBrowserTab](Developer%20Hub/Frontend/src/controller/AgentHubController.ts#L215)
so call sites don't have to remember the shape of the params object.

```ts
await callNavigationOpenBrowserTab(workloadClient, url);
```

## Shared helper — `openExternalTab`

Every site that needs to open an external tab (GitHub sign-in, "Open
in Fabric" links, session artifacts, Fabric workspace/lakehouse
previews, attachment downloads…) goes through the same helper so we
have exactly one implementation of the retry / allowlist-bypass /
fallback logic:

File: [openExternalTab.ts](Developer%20Hub/Frontend/src/components/AgentHub/openExternalTab.ts)

```ts
import { openExternalTab, externalLinkOnClick } from "./openExternalTab";

// Programmatic open (after an await-chain, in a button handler, etc.)
await openExternalTab(workloadClient, url, {
    onFallback: (u) => setBannerUrl(u),   // optional: show "copy URL" UI
});

// Anchor click-handler factory — preserves Ctrl/Cmd/Shift/middle-click
<a href={url} target="_blank" rel="noopener noreferrer"
   onClick={externalLinkOnClick(workloadClient, url)}>
    Open in Fabric
</a>
```

What the helper does, in order:

1. **SDK attempt.** `workloadClient.navigation.openBrowserTab({ url,
   queryParams: {} })`. Uses `.call()` so the SDK's `this` binding is
   preserved even when the caller destructures the method off the
   object. Treats `undefined` return as success (older builds),
   `{ success: false }` as failure.
2. **Strip `experience=` retry.** Fabric's host URL allowlist
   frequently rejects full portal URLs that carry the `experience`
   query param. The helper detects it, removes it, and retries.
3. **`window.open` fallback.** Works in Storybook, Playwright, and
   local dev where the iframe is absent or has `allow-popups`.
4. **`onFallback(url)` callback.** Caller can render a banner/dialog
   telling the user to paste the URL manually. The URL has already
   been copied to the clipboard (best-effort — the sandbox may block
   clipboard access too).

`externalLinkOnClick` wraps the programmatic call so `<a
target="_blank">` anchors still honour modifier clicks (Ctrl/Cmd =
new tab, Shift = new window, middle-click). The browser's native
"open in new tab" UI bypass is not affected by the popup blocker,
so those modifier clicks keep working without the SDK.

### Call-sites currently using the helper

| Site | File |
|------|------|
| GitHub sign-in button | [AgentHubLayout.tsx](Developer%20Hub/Frontend/src/components/AgentHub/AgentHubLayout.tsx) |
| GitHub device-flow fallback link | [AgentHubLayout.tsx](Developer%20Hub/Frontend/src/components/AgentHub/AgentHubLayout.tsx) |
| Session artifact "Open" button | [mission/MissionControlPage.tsx](Developer%20Hub/Frontend/src/components/AgentHub/mission/MissionControlPage.tsx) |
| "Open in Fabric" error recovery link | [OrchestratorPage.tsx](Developer%20Hub/Frontend/src/components/AgentHub/OrchestratorPage.tsx) |
| Workspace preview item "Open" icon | [WorkspacePreviewModal.tsx](Developer%20Hub/Frontend/src/components/AgentHub/WorkspacePreviewModal.tsx) |
| Attachment download mint URL | [OrchestratorPage.tsx](Developer%20Hub/Frontend/src/components/AgentHub/OrchestratorPage.tsx) (inline — predates the helper; keeps its own "Download ready" banner) |

New "open a link" features should always import from
`./openExternalTab`. Do not reintroduce raw `window.open` or anchor
`target="_blank"` without an `onClick` that routes through the
helper — it will silently fail inside Fabric.

## Real-world use: one-click GitHub Sign-in

This is the flow that surfaced the pattern. Historically, re-opening
the workload after closing the browser showed the GitHub device-flow
card (enter a 6-char code at `github.com/login/device`). GitHub
returns a second URL in the device-flow response —
`verification_uri_complete` — which has the user code pre-embedded.
Opening *that* URL drops the user on the Authorize screen directly
and uses their cached GitHub session.

We tried `window.open(verificationUriComplete)` first — blocked by the
sandbox. Switching to `workloadClient.navigation.openBrowserTab` made
the "Sign in with GitHub" button open a new tab reliably, because the
portal — not the iframe — is the one opening it.

### Sequence

1. User clicks **Sign in with GitHub** inside the iframe.
2. Frontend hits `POST /api/github/device-code`; backend returns the
   device code, user code, and `verification_uri_complete`.
3. Still in the same click gesture, the frontend calls
   `workloadClient.navigation.openBrowserTab({ url:
   verification_uri_complete, queryParams: {} })`.
4. The portal opens `https://github.com/login/device?user_code=XXXX-XXXX`
   in a new tab. GitHub's cached session takes over: user clicks
   **Authorize**.
5. Meanwhile the frontend polls `POST /api/github/poll-token` until
   GitHub returns the access token, then persists it in
   `localStorage` (see
   [useGitHubAuth.ts](Developer%20Hub/Frontend/src/components/AgentHub/useGitHubAuth.ts#L30)
   ).
6. On the next render, the auth gate drops and the dashboard appears.

### Code

```tsx
<Button
    appearance="primary"
    onClick={async () => {
        const flow = await auth.startDeviceFlow();
        if (!flow) return;
        const url = flow.verificationUriComplete || flow.verificationUri;
        try {
            await workloadClient.navigation.openBrowserTab({
                url,
                queryParams: {},
            });
        } catch {
            // Fallback: the device-code card still renders the code +
            // link so the user can continue manually.
        }
    }}
>
    Sign in with GitHub
</Button>
```

The device-flow card is still rendered as a fallback so users running
outside Fabric (local dev, storybook, tests) have a manual path.

## When to use this

Use `openBrowserTab` for:

- External OAuth / device-flow verification URLs.
- Docs links (Microsoft Learn, GitHub repo, release notes).
- Third-party tools that require a full browser context (e.g. the
  Fabric portal itself, or a linked lakehouse).
- Anything where `<a target="_blank">` silently fails in testing.

Do **not** use it for:

- In-app route changes — use `history.push` / the editor-tabs API.
- Anything that needs the result back in the workload — the new tab
  has no handle to the iframe. Poll your own backend instead, the way
  the GitHub flow does.

## Gotchas

- **Fabric host allowlist.** `openBrowserTab` only accepts URLs whose
  origin is allowlisted by the Fabric portal. The list is:
  1. Microsoft first-party (`*.microsoft.com`, `*.fabric.microsoft.com`,
     `*.powerbi.com`, `login.microsoftonline.com`, `aka.ms`, …).
  2. A handful of well-known OAuth providers (`github.com`,
     `githubusercontent.com`) — that's why the device-flow URL works.
  3. **Our own workload origins**, declared in `WorkloadManifest.xml`
     via the `${WORKLOAD_BACKEND_URL}` / `${WORKLOAD_FRONTEND_URL}`
     placeholders. These are substituted at package-build time from
     `Developer Hub/.env` (see `.env.example`). Defaults are legacy
     placeholders; production builds override both.

  Anything else — including `http://127.0.0.1:5000`,
  `http://localhost:5000`, and the dev-gateway `http://127.0.0.1:60006`
  — makes the helper return `{ success: false }` and the `onFallback`
  branch runs. For features that mint a backend URL (attachment
  download), this means dev falls back to the in-frame blob path while
  prod uses `openBrowserTab` against the registered HTTPS endpoint.
- **HTTPS required for non-Microsoft origins.** Fabric rejects `http://`
  URLs for any non-Microsoft host even when the host itself is on the
  allowlist. Point `WORKLOAD_BE_URL` at an HTTPS tunnel (e.g. the
  dev-gateway HTTPS proxy or an `ngrok` tunnel) to exercise the
  `openBrowserTab` path locally.
- **First-time Fabric portal permission prompt.** On the very first
  cross-origin hop some browsers show a one-time "Allow Fabric to open
  new tabs?" banner. There's nothing we can do about it in code; it's
  the platform's consent gate.
- **Inside `await` chains.** If you `await` network work before calling
  `openBrowserTab`, you may lose the user-activation window. Kick the
  network request off and call `openBrowserTab` as soon as you have the
  URL, *then* continue awaiting downstream work. For the GitHub flow
  we're fine because `/api/github/device-code` resolves in <200 ms and
  Chromium's transient activation window is ~5 s.
- **Local dev outside Fabric.** `workloadClient` is still provided in
  dev (stubbed by the workload SDK), but `openBrowserTab` may throw.
  Always wrap in `try/catch` and keep the visual fallback.
- **Sign-out doesn't clear GitHub's browser session.** `signOut()` in
  `useGitHubAuth` only drops our locally-cached token. Next sign-in
  therefore re-uses the cached GitHub session — that's *why* the
  one-click flow feels magical, but be aware when debugging auth
  issues: clear `localStorage` **and** GitHub cookies to reproduce a
  true cold start.
