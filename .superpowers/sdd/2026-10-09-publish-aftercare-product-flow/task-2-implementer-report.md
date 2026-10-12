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

## Review Fix Round 2

修复前端已经发送 `filter.versions`、Java 后端却接收后丢弃的问题：

- `ExportRequest.ExportFilter` 新增 nullable `List<Integer> versions`；缺省或空数组继续表示全部版本，兼容旧请求和旧作业快照。
- `ExportSpec` 校验版本号必须为正整数，并规范化为去重、升序列表；规范化后的字段参与幂等 spec equality 和 API 回显。
- 创建作业时只冻结筛选命中的发布来源；worker 从持久化 filter 再次约束 `ExportPlan`，只对这些来源对应的 engine sid 执行投影查询。
- 前端 Zod 将 `versions` 收紧为后端真实 DTO 的必有 nullable 字段，并同步测试夹具。
- 数据库核对确认 `survey_published_version.version_no`、`response_export_item.version_no` 和作业 `snapshot_version` 均为 `integer`。版本与投影来源通过 `(engine_instance_id, engine_sid)` 映射，因此不需要 migration 或额外 SQL 条件；快照测试直接查询 `response_export_item.version_no` 证明过滤生效。

### TDD 与验证

- RED：先新增 Java API/worker 快照回归，`ResponseExportApiTest` 出现 3 个预期失败：非法版本返回 202、规范化版本未参与幂等 spec、API 响应和快照缺少版本约束。
- GREEN：定向 `ResponseExportApiTest` 8/8 通过；版本 2 的作业回显 `[2]`、快照只含版本 2、最终 CSV 也只含版本 2。
- 全部 `ResponseExport*Test`：12 个测试类、55 个测试全部通过，覆盖规模、一致性、恢复、权限、租户隔离和全部导出格式。
- Admin Web 聚焦测试：2 个文件、17 个测试全部通过。
- Admin Web 全量测试：25 个文件、211 个测试全部通过。
- TypeScript typecheck、ESLint、production build 和 `git diff --check` 全部通过；build 仅有既存的主 chunk 体积提示。
- 未修改或暂存 `platform/deploy/production/**` 及 Task4 的 `platform/README.md` 改动。

## Review Fix Round 3

收紧 `filter.versions` 的 JSON 输入边界，避免 Jackson 将非整数值静默转换为版本号：

- 新增 `PublishedVersion` JSON 值类型，在反序列化阶段直接检查原始 `JsonNode`，只接受可落入 Java `int` 且大于 0 的 JSON integer token。
- 明确拒绝浮点数（包括 `2.0`）、数字字符串、布尔值、数组内 `null`、非正数和 `int` overflow；统一返回既有 `400 invalid_request` envelope。
- `versions` 字段缺省或字段值为 `null` 仍表示全部版本，保持旧请求和旧作业兼容。
- 有效版本仍以 JSON number 回显，并在 `ExportSpec` 中去重、升序，继续参与幂等 spec equality、快照筛选和 worker 查询。

### TDD 与验证

- RED：先加入 MockMvc 边界测试，`[1.5]` 被旧实现错误接受为 202 并回显为 `[1]`，证明存在 coercion。
- GREEN：`ResponseExportApiTest` 8/8 通过；非法 token 均返回 `400 invalid_request`，缺省/字段 `null` 和有效整数行为不回归。
- 全部 `ResponseExport*Test`：12 个测试类、55 个测试全部通过，0 failure/error。
- Admin Web 聚焦测试：6 个文件、77 个测试全部通过；全量测试：25 个文件、211 个测试全部通过。
- TypeScript typecheck、ESLint 和 production build 全部通过；build 仅有既存的主 chunk 体积提示。
- 未修改 production deployment 或 Task4 文件。
