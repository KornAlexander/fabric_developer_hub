# Mission Control Redesign UX Audit

Date: 2026-04-25
Scope: `Frontend/src/components/AgentHub/mission/MissionControlPage.tsx`, Mission Control SCSS, and focused Playwright coverage.

## Problem Statement

The execution page was visually fighting the user's primary job: reading what happened. The log area was too small, layout controls used unclear copy, artifacts were duplicated in the right rail and bottom dock, and draft output copy made normal planned outputs look broken.

## Findings

- Visual hierarchy: logs must be the dominant surface. The previous split made the run overview and duplicate artifacts compete with the live trace.
- Spacing and rhythm: the right rail used heavy uppercase section titles and large artifact labels, which increased noise in a dense operational UI.
- Microcopy: `Artifacts so far` and `Draft - not yet written` were too internal and alarmist for user-facing execution status.
- Affordance: `Focus logs` / `Restore layout` read like two unrelated commands instead of one layout mode switch.
- Responsiveness: the log needed stable height at desktop and narrow widths so the user can scan more than a tiny slice of output.

## Iterations

1. Log-first layout: narrowed the overview rail, removed the bottom artifact dock, raised the log viewport height, and made the main column a single log surface.
2. Control redesign: replaced the focus/restore command button with a segmented `Standard` / `Expanded log` layout control.
3. Output language pass: renamed the rail section to `Outputs` and replaced draft copy with state labels: `Planned output`, `Waiting for approval`, `Needs issue review`, and `Available`.

## Validation Evidence

- Desktop standard layout: [docs/screenshots/mission-control-redesign/desktop-standard.png](screenshots/mission-control-redesign/desktop-standard.png)
- Desktop expanded log layout: [docs/screenshots/mission-control-redesign/desktop-expanded-log.png](screenshots/mission-control-redesign/desktop-expanded-log.png)
- Narrow viewport layout: [docs/screenshots/mission-control-redesign/mobile-standard.png](screenshots/mission-control-redesign/mobile-standard.png)

## Automated Checks

Focused Playwright coverage in `Frontend/e2e/mission-control-redesign.spec.ts` verifies:

- `Draft - not yet written`, `Artifacts so far`, and `Latest artifacts` are absent.
- `Outputs` and `Planned output` are visible for draft output events.
- The log column is more than twice the overview rail width on desktop.
- Expanded log mode hides the rail and grows the log surface.
- Desktop and narrow screenshots render without critical text overflow.

Focused command run:

```bash
cd "Developer Hub/Frontend" && npx playwright test e2e/mission-control-redesign.spec.ts --project=chromium --reporter=list
```

Result: 1 test passed.

Regression command run:

```bash
cd "Developer Hub/Frontend" && npx playwright test e2e/mission-control-redesign.spec.ts e2e/new-session-experience-visual.spec.ts --project=chromium --grep "Mission Control redesign|New Session end-to-end experience" --reporter=list
```

Result: 2 tests passed.

## Residual Risk

Existing portal architecture E2E assertions were updated from `Artifacts so far` to `Outputs`. Full Fabric portal tests still depend on authenticated external state and were not run as part of this focused local validation.