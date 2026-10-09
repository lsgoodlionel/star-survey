# Publish Aftercare Task 2 Implementer Report

## Scope

- Added the post-publish access panel with delivery-link creation, copy, revocation, backend QR rendering, response summary, and response-workspace entry.
- Added the response workspace with summary, status/version filters, cursor pagination, permission-aware detail states, and masked-answer presentation.
- Added export creation for the five backend-supported formats (`csv`, `xlsx`, `sav`, `docx`, `attachments`), live job status, progress, cancellation, secure client download, and expired-state handling.
- Added the tenant-scoped `responses` route and workflow tab while preserving the selected question query parameter.
- Added responsive layouts for narrow screens and overflow containment for response tables and long URLs.

## TDD Evidence

RED was observed before implementation:

- `PublishedAccessPanel.test.tsx` and `ResponsesPage.test.tsx` failed because their production modules did not exist.
- `SurveyShell.test.tsx` and `PublishPage.test.tsx` failed because the response route/tab and post-publish panel were absent.

GREEN after implementation:

- Focused Task 2 suite: 61/61 passed.
- Full Admin Web suite: 206/206 passed.
- TypeScript typecheck: passed.
- ESLint: passed.
- Production build: passed; only the pre-existing main-chunk size warning remains.
- Scoped `git diff --check`: passed.

## Behavioral Notes

- Delivery mutations invalidate only the exact tenant/survey delivery-list and response-summary keys.
- QR images use the backend binary endpoint and revoke generated object URLs on cleanup.
- Revoked links cannot be copied and no QR is rendered for them.
- Summary and detail permissions fail independently; summary-only users retain statistics while detail/export controls stay hidden.
- Export formats are derived from the reviewed typed client contract; no unsupported format is displayed.
- No production deployment files were edited or staged by this task.

## Commit

`aa2bb2d7 feat: add publish aftercare and response workspace`

## Review Fix Round 1

针对 `task-2-review.md` 的四项 Important 完成以下修复：

- 将已撤销、已过期和非 HTTP(S) 的不可用投放链接标记为不可操作，分别显示中文原因，并移除打开、复制、二维码和撤销入口。
- 导出请求现在携带与答卷列表一致的发布版本筛选，并由 shared API 契约校验；测试精确断言完整请求体。
- 导出状态轮询失败时保留当前任务并提供“重新加载任务状态”；取消失败时保留任务并提供“重试取消”和“刷新任务状态”，两种错误分别有独立状态测试。
- 发布页的“答卷与导出”入口复用 `surveyWorkflowHref`，完整保留当前 `question` 查询上下文。

### TDD 与验证

- 前端新增测试首次运行出现 6 个预期失败，分别覆盖不可用链接、版本筛选请求、轮询/取消错误恢复和查询上下文。
- 聚焦 Admin Web：6 个文件、77 个测试全部通过。
- 全量 Admin Web：25 个文件、211 个测试全部通过。
- TypeScript typecheck、ESLint、production build、`git diff --check` 全部通过；build 仅有既存的主 chunk 体积提示。
- 未编辑或暂存 production deployment 文件；工作区中对应改动属于并行 production agent。
