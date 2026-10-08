# Task 5 Report — 文本导入与草稿预览

## Commit

- Pending: `feat: add survey import and draft preview`

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
