# Task 5 Report — 文本导入与草稿预览

## Commit

- Initial: `47f8ec7e feat: add survey import and draft preview`

## TDD Evidence

- RED: `npm test -- --run src/features/import src/features/preview` failed because `ImportPage` and `PreviewPage` did not exist.
- RED: added source-change regression; focused test failed because a preview could still be confirmed after its source text changed.
- GREEN: focused import/preview suite passed: 2 files, 9 tests.

## Delivered

- Added runtime-validated import preview and confirmation API adapters.
- Added permission-aware two-step text import with legal questions selected by default, original line diagnostics, target group selection, current draft version optimistic locking, and success cache refresh/navigation.
- Preserved source text and checked ordinals on 409; on 422, re-runs the read-only preview with the same text to recover line-level server diagnostics without changing shared API error behavior.
- Invalidates an old preview whenever the source text changes, preventing stale ordinals from being submitted against different text.
- Added read-only draft preview for X/L/M/S/T question types and readable unknown-type placeholders.
- Added fixed-width desktop/mobile segmented preview modes and protected import/preview routes.

## Verification (Node 22.23.2)

- `npm test -- --run src/features/import src/features/preview`: 9/9 passed.
- `npm test -- --run`: 100/100 passed across 10 files.
- `npm run lint`: passed.
- `npm run typecheck`: passed.
- `npm run build`: passed; Vite transformed 2080 modules.
- `npm run assert:production-bundle`: passed; 11 generated files checked.
- `git diff --check`: passed.

## Files

- `platform/apps/admin-web/src/shared/api/imports.ts`
- `platform/apps/admin-web/src/features/import/ImportPage.tsx`
- `platform/apps/admin-web/src/features/import/ImportPreviewTable.tsx`
- `platform/apps/admin-web/src/features/import/ImportPage.test.tsx`
- `platform/apps/admin-web/src/features/import/import.css`
- `platform/apps/admin-web/src/features/preview/PreviewPage.tsx`
- `platform/apps/admin-web/src/features/preview/DraftRenderer.tsx`
- `platform/apps/admin-web/src/features/preview/PreviewPage.test.tsx`
- `platform/apps/admin-web/src/features/preview/preview.css`
- `platform/apps/admin-web/src/app/router.tsx`

## Concerns

- The shared `ApiClient` intentionally reduces 422 responses to a safe generic `ApiError` and does not expose response `problems`. Task 5 therefore performs one additional non-writing preview request after an import 422 to display current line-level diagnostics. If the shared error contract later exposes structured validation details, this fallback can be removed.
- Draft preview is intentionally renderer-only: it has no answer state, form submission, or engine request path.

## Review Fix Round 1

### Findings addressed

- Added a source revision snapshot to preview mutations. Responses and errors from an older text revision are ignored, and changing source text resets the obsolete mutation view before a new preview.
- Keyed import and preview state by `tenantId + surveyId`; route parameter and tenant changes synchronously mount a fresh instance, so text, preview, checked ordinals, target group, errors, and device mode cannot cross identities.
- Scoped all Task 5 draft and capability reads, cache writes, and invalidations by tenant. Import success also writes Task 4's existing legacy `['survey-draft', surveyId]` key so immediate navigation to the unchanged editor consumes the imported draft.
- Replaced incomplete tab semantics with ordinary device buttons using `aria-pressed`, grouped under an accessible device control label.

### TDD evidence

- RED: focused tests failed in five expected places: stale deferred preview became visible, survey B retained survey A text, tenant B reused tenant A draft cache, tenant-scoped success cache was absent, and preview controls still exposed incomplete tab semantics.
- GREEN: focused import/preview suite passed: 2 files, 12 tests.
- Added regressions for deferred A-to-B preview changes, A-to-B survey route changes including text/preview/selection/group isolation, same survey UUID across tenants, tenant-scoped cache updates plus editor compatibility write, and `aria-pressed` device controls.

### Verification (Node 22.23.2)

- `npm test -- --run src/features/import src/features/preview`: 12/12 passed.
- `npm test -- --run`: 103/103 passed across 10 files.
- `npm run lint`: passed.
- `npm run typecheck`: passed.
- `npm run build`: passed; Vite transformed 2080 modules.
- `npm run assert:production-bundle`: passed; 11 generated files checked.
- `git diff --check`: passed.

### Remaining concern

- Resolved in Review Fix Round 2: the approved scope expansion migrated Task 4 editor and the publish/version flow to the same canonical tenant-aware keys. The temporary legacy draft compatibility write was removed.

## Review Fix Round 2

### Approved scope expansion

- Expanded beyond the original Task 5 modules to the frontend editor, publish workflow, immutable version detail, and shared survey/approval API key factories.
- No Task 7 files, authentication cache-clearing behavior, backend code, or generated output were changed.

### Findings addressed

- Added canonical `surveyDetailQueryKey`, `surveyDraftQueryKey`, and `surveyCapabilitiesQueryKey` factories with mandatory `tenantId` and `surveyId` parameters.
- Migrated publish overview, approval requests, versions, and version-detail factories to mandatory tenant-aware signatures; removed the old publish capability key and all tenant-less aliases.
- Updated EditorPage, ImportPage, PreviewPage, PublishPage, and VersionDetailPage to consume canonical factories. Route pages pass `session.me.tenantId` through to publish/version consumers.
- Removed ImportPage's legacy `['survey-draft', surveyId]` write. Import success now writes the exact canonical draft key consumed by EditorPage and invalidates the exact overview, approval-request, and versions keys consumed by PublishPage.
- Preserved editor recovery isolation by leaving the existing `tenantId + surveyId` recovery registry contract unchanged.

### TDD evidence

- RED: focused tests had four expected failures: Editor and Publish reused fresh tenant A data after switching to tenant B with production `staleTime`, while import success missed the new canonical draft and publish cache keys.
- GREEN: import, preview, editor, and publish focused suites passed: 6 files, 78 tests.
- Added two production-configuration regressions using `staleTime: 30_000`: same survey UUID across tenants in EditorPage and PublishPage cannot display or consume tenant A data in tenant B.
- Strengthened import success coverage to pre-populate real factory keys and assert overview, approval requests, versions, and nested version detail invalidation plus canonical editor draft handoff.

### Verification (Node 22.23.2)

- `npm test -- --run src/features/import src/features/preview src/features/editor src/features/publish`: 78/78 passed.
- `npm test -- --run`: 105/105 passed across 10 files.
- `npm run lint`: passed.
- `npm run typecheck`: passed.
- `npm run build`: passed; Vite transformed 2080 modules.
- `npm run assert:production-bundle`: passed; 12 generated files checked.
- `git diff --check`: passed.

### Remaining concerns

- No remaining cache-isolation concern within the approved frontend scope. Query cache isolation no longer relies on AuthProvider clearing data between tenants.
- The previously documented 422 diagnostic fallback remains: because the shared API error model does not expose structured validation problems, ImportPage performs one additional read-only preview request to recover line details.
