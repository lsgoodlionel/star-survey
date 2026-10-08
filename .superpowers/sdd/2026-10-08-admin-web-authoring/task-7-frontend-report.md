# Task 7 frontend/E2E slice report

## Scope

- Owned paths: `platform/apps/admin-web/**` and this report.
- Stack paths under `platform/deploy/test/**` and `platform/tests/e2e/**` were not modified or staged by this slice.
- Fixed handoff: `ADMIN_WEB_BASE_URL`, mode-0600 `ADMIN_WEB_JWT_FILE`, optional non-secret `ADMIN_WEB_METADATA_FILE`, and sanitized `ADMIN_WEB_RESULT_FILE`.

## TDD evidence

- RED, focused Vitest: 3 expected failures proved the missing E2E token route, 180-second publish timeout, and version-detail link.
- RED, Playwright desktop: the authoring journey was discovered and started; launch failed because Chromium revision 1248 is not installed. No browser was downloaded, per controller instruction.
- GREEN, focused Vitest on Node 22.23.2: 42/42 passed.
- GREEN, full Vitest on Node 22.23.2: 108/108 passed after excluding `e2e/**` from Vitest discovery.

## Implementation

- Added Node 22 multi-stage Docker image and nginx runtime. Runtime receives only `dist`, serves SPA fallback and cached static assets, emits security headers, and proxies only `/v1` plus `/actuator/health` to `PLATFORM_UPSTREAM`.
- Added an E2E-only in-memory token route gated by `VITE_E2E=true`. Standard production output rejects all development/E2E token markers; E2E output requires them.
- Set publish request timeout to 180 seconds and added UI links for each immutable published version.
- Added Playwright desktop/mobile projects with trace, video, and automatic screenshots disabled. A failed test first clears and hides sensitive controls, then takes one manual screenshot; if the page cannot be sanitized, no screenshot is written. Recorded network evidence contains only method, URL, and status; no headers, bodies, or token are written.
- Desktop journey uses business pages for login, project/folder/survey creation, editing, import, preview, approval, publishing, and UI navigation into version details. Mobile creates its own survey through the UI and checks tabs, focus outline, primary action reachability, and horizontal overflow.

## Verification

- `npm test -- --run`: 10 files, 108 tests passed.
- `npm run lint`: passed.
- `npm run typecheck`: passed, including Playwright config/spec.
- Standard `npm run build && npm run assert:production-bundle`: passed, 2080 modules and 12 files checked.
- `npm run build:e2e && npm run assert:e2e-bundle`: passed, 2082 modules and 14 files checked.
- E2E Docker image build passed, including E2E bundle assertion.
- Runtime `nginx -t` passed with a resolvable test upstream.
- `git diff --check`: passed.

## Remaining integration concern

- The real browser journey is pending the stack slice and Chromium availability. The controller must install the pinned Playwright Chromium revision and run the shared-stack command; this slice intentionally did not download a browser or alter stack-owned files.

## Review fix round 1

- RED: `browserGateHardening.test.ts` produced four expected failures for automatic screenshots, a visible token control, the broad `/v1` nginx prefix, and non-interactive mobile tab checks.
- Screenshot hardening: Playwright automatic screenshots remain off; failure capture sanitizes before manually taking a screenshot and skips capture when sanitization cannot complete. Login always clears the token in `finally`, the UI uses a password input, URLs redact raw and encoded token forms, and result writing rejects any remaining token.
- Proxy hardening: nginx now has separate `location = /v1` and `location ^~ /v1/` proxy blocks. `/v1evil` is handled by the SPA and cannot reach the platform upstream.
- Mobile hardening: the test clicks all three tabs, verifies `aria-selected` and each named panel, checks the tab/panel/control/save action in the viewport with non-zero dimensions, checks center-point occlusion, uses keyboard focus, and rechecks horizontal overflow after every panel.
- Node 22.23.2 focused contracts: 8/8 passed. Full Vitest: 12 files and 116/116 passed. Lint and typecheck passed.
- Standard and E2E builds plus their bundle assertions passed; standard `dist` was restored last.
- Docker image build and `nginx -t` passed. Container curl evidence: `/` 200, `/v1` 502, `/v1/probe` 502, `/v1evil` 200 with the configured security headers. The 502 responses are expected because the verification upstream was deliberately set to closed `127.0.0.1:9`.

## Integration fix

- RED: the Playwright configuration test proved a runner-provided `ADMIN_WEB_TEST_RESULTS_DIR=/private/tmp/admin-web-run-42/test-results` was ignored in favor of the shared local directory.
- Playwright now passes `ADMIN_WEB_TEST_RESULTS_DIR` through unchanged as `outputDir`; local runs without the variable retain `./test-results`.
- The focused configuration tests cover both the isolated runner path and the local fallback.

## Real-stack debugging fix round 2

- Real-stack RED evidence: both browser projects exhausted the 240-second test timeout. Desktop received `/v1/me` 200 but looked for the actor in shell text; mobile independently blocked while creating a second workspace; failure screenshot cleanup inherited the exhausted test budget.
- Login now parses the real `/v1/me` JSON response and compares its `actorId` and `tenantId` to runner metadata in both projects. It no longer relies on visible account text.
- `chromium-mobile` now depends on `chromium-desktop`. Desktop alone creates and publishes the survey, writes `ADMIN_WEB_RESULT_FILE` only after all version assertions pass, and mobile reads that completed result without overwriting it.
- Failed-test screenshot work gets at most seven additional seconds, returns immediately for a closed page, budgets sanitization at 1.5 seconds and screenshot capture at 2 seconds, and emits no screenshot when sanitization fails.
- Node 22.23.2 verification: focused contracts 10/10, full Vitest 13 files and 121/121, lint, typecheck, standard/E2E builds and both bundle assertions passed. `playwright --list` reports exactly two projects and two scenarios.
