# Task 4 Implementer Report

日期：2026-10-09
分支：`feat/admin-product-alignment`
任务：Real preview UI and full product journey

## 结论

Task 4 已按 brief / plan / spec 完成。管理端保留本地快速预览，并接入隔离 LimeSurvey 真实预览 session；真实浏览器门禁现覆盖创建问卷、隔离预览作答、正式计数保持 0、审批发布、链接与二维码、正式作答、答卷可见、CSV 导出创建与下载。Production 层仍为 `not_started`，未因本任务的开发/测试证据提升为生产验收通过。

## 实现

- 新增类型化 previews client，使用 Zod 校验完整 session 结构，查询键包含 tenant / survey / session，支持 create / get / close、超时和 `202` / `502` 携带 session body。
- PreviewPage 同时保留“快速预览”和“真实预览”；创建中防重复提交，展示中文状态、草稿版本和有效期；通过新窗口打开，不使用 iframe；支持显式结束与 cleanup failure 重试；失败保留同一 request ID 摘要。
- 真实预览操作按钮在移动端最小高度为 44px；桌面和 Pixel 7 均验证无横向页面溢出。
- Admin Web E2E 使用同源 Nginx 将 platform、gateway preview access 和 LimeSurvey runtime 串成一个真实浏览器入口，所有 host 端口仅绑定随机 loopback。
- E2E 测试栈补齐独立 delivery secret，并在浏览器执行期间运行真实 `MjyPlatformBridge` cron，以匹配正式答卷事件的后台投递模型。
- 完整旅程使用真实 platform / gateway / LimeSurvey / PostgreSQL / MariaDB，不写假答卷、不直接写投影库、不使用静态假数据。
- 更新 capability 六层证据和需求追踪；预览、投放链接、答卷前端/流程证据已更新，导出 flow 仍为 partial，production 仍为 not_started。

## E2E 证据

- 桌面：`platform/docs/productization/evidence/task4/real-preview-desktop.png`
- 移动：`platform/docs/productization/evidence/task4/real-preview-mobile.png`
- `run-admin-web-e2e.sh --fresh`：11/11 Playwright tests 通过；gate 同时核对 platform API、platform DB、gateway publish 次数和 engine DB active binding。
- 正式作答先经 bridge event log 和 cron 投递，再由 platform summary API 返回完成数 1；答卷页回读到真实字段 `Q9` 和存储码 `A1`。
- 导出任务由真实 `ResponseExportWorker` 完成 1 行 CSV 制品，并通过浏览器下载事件验收。

## 验证

- `npm test -- --run`：26 files / 219 tests 通过。
- `npm run typecheck`：通过。
- `npm run lint`：通过。
- `npm run build`：通过；仅保留既有 Vite 大 chunk 提示。
- `platform/deploy/test/run-admin-web-e2e.sh --fresh`：通过，11/11。
- `platform/deploy/test/run-p1-e2e.sh`：通过；覆盖 v1/v2/v3 发布、旧版收口、两版答卷投影和幂等性。
- `python3 platform/tools/productization/render_capabilities.py --check`：通过。
- `python3 -m reqtrace.cli check`：通过；305 条需求、84 条证据、覆盖 40 条需求。
- `git diff --check`：通过。

## 发现与边界

1. Task 3 gateway 在完全空的 LimeSurvey 中将 `list_surveys` 的 `ERR_NO_DATA` 当成未知错误，首次 preview 会停在 creating。Task 4 E2E 通过 `seed-empty-engine.py` 导入一份真实、可丢弃的 fixture 作为首预览 reconciliation baseline；未跨边界修改已 CLEAN 的 Task 3 后端。后续应在 preview gateway 独立修复空列表语义。
2. 答卷页面遵循全局 30 秒 `staleTime`。若发布前看过 0 答卷并在正式作答后立即返回，短窗口内可能先显示旧摘要；E2E 等缓存 stale 后通过正常导航验证真实 refetch。自动刷新策略应由答卷工作区任务单独决定。
3. 标准单选答案当前回读为 LimeSurvey 存储码（例如 `A1`），未解析为“男”等选项标签；本任务按真实后端值验收，没有伪造展示标签。
4. Task 3 后端关闭日志中的 expires 显示为前一日，是 close 机制用于立即收口的引擎值，不影响 session API 返回的原始有效期展示。
5. 已知 `platform/deploy/demo/init-platform-db.sh` executable bit 问题未阻断本任务两个 E2E 门禁，因此没有修改其 mode。

## 范围控制

- 未修改或暂存 `.github/workflows/release.yml`、`platform/deploy/production/**` 或任何 release workflow 文件。
- 工作区中并行 Release agent 的未提交文件保持原样，并将从本任务提交中排除。

## Review Fix Round 1/5

### 收口结果

- Preview pending 查询在短暂错误后继续轮询，页面显示中文错误和手动恢复入口；ready 按 `expiresAt` 当地收口为已过期。
- closed / expired 可用新 request ID 再次创建；failed 同时保留同 request ID 重试和新建选项；旧 session 进入页面审计历史。
- 答卷 summary/list 不再受全局 30 秒 fresh 窗口限制，页面新增常显“刷新答卷数据”；E2E 删除 `30_500ms` 固定等待，改为 60 秒上限的 UI eventual。
- 隔离预览作答事件确认已由 bridge 投递后，E2E 直接比较 `survey_published_version`、
  `survey_question_binding`、official `survey_route`、`engine_outbox` 和 `response_projection`；五项前后均为 0。
- 二维码等待 `naturalWidth/naturalHeight > 0`，再用 `jsQR` 解码实际渲染像素，结果与刚创建的正式作答 URL 逐字相等。
- 浏览器下载后解包实际 ZIP，读取 `responses.csv` 与 `fields.csv`，校验 UTF-8 BOM、CRLF、
  字段字典映射、一条正式答卷和真实存储值 `A1`。

### TDD 与验证

- RED：新增的轮询恢复、终态新建/历史、过期收口和答卷手动刷新用例按预期失败。
- GREEN：定向组件测试 17/17，Admin Web 全量 26 files / 223 tests 通过。
- `npm run typecheck`、`npm run lint`、`npm run build` 通过；build 仅有既有 chunk size 提示。
- `platform/deploy/test/run-admin-web-e2e.sh --fresh` 通过，11/11；包含五项隔离快照、二维码解码和 CSV 内容验证。
- `platform/deploy/test/run-p1-e2e.sh --fresh` 通过；覆盖 v1/v2/v3、旧版收口、两版答卷投影和幂等回归。
- capability render check 通过；traceability 仍为 305 条需求、84 条证据、覆盖 40 条需求。
- 屏幕证据已重新生成：`platform/docs/productization/evidence/task4/real-preview-desktop.png` 与
  `platform/docs/productization/evidence/task4/real-preview-mobile.png`。
- Production 仍为 `not_started`；导出 flow 仍为 `partial`，未用 CSV 单旅程替代其他格式的专项证据。
- 本轮已解决上文“发现与边界”第 2 项的 30 秒缓存可观测性问题；其余边界仍保留。
