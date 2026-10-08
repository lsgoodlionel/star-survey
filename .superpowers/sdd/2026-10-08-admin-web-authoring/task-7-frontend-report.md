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
- Added Playwright desktop/mobile projects with trace and video disabled. Failure screenshots remain enabled. Recorded network evidence contains only method, URL, and status; no headers, bodies, or token are written.
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
