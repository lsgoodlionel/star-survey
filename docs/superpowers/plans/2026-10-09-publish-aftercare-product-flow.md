# Publish Aftercare Product Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将发布后的答卷链接、二维码、答卷摘要、明细和导出接入统一问卷上下文，并提供不污染正式数据的 LimeSurvey 真实运行时预览。

**Architecture:** Admin Web 新增类型化 delivery/response/export/preview 客户端和「答卷与导出」路由。已有投放、答卷和导出 API 保持不变；隔离预览由 Platform 创建短期 preview session，Gateway 在独立 SID/世代中发布草稿快照，回收时关闭引擎问卷并不写入正式版本映射。

**Tech Stack:** Java 21/Spring Boot、PostgreSQL/Flyway、Python 3.12 Gateway、React 19、Zod、TanStack Query、Vitest、Playwright。

**Spec:** `docs/superpowers/specs/2026-10-09-platform-productization-production-upgrade-design.md`

## Global Constraints

- 不改变现有投放链接签名、答卷权限、导出快照和下载再授权语义。
- preview session 必须带租户、问卷、草稿版本、操作人、引擎绑定、到期时间和状态；跨租户 ID 返回 404。
- 预览创建必须幂等，到期/手动关闭必须可重试；清理失败留运维可见告警。
- 前端所有状态、格式和错误用中文表达，原始枚举只在折叠技术信息中显示。

## Review Focus

- 正式发布版本、投放链接和正式答卷计数在预览前后必须完全一致。
- 无明细权限角色可以看摘要但看不到行和导出数据；页面必须给出可操作的权限说明。
- 二维码使用后端 SVG/PNG 输出，前端不重新编码 URL；撤销或过期链接不能被复制为可用链接。

---

### Task 1: Typed delivery, response and export clients

**Files:**
- Create: `platform/apps/admin-web/src/shared/api/delivery.ts`
- Create: `platform/apps/admin-web/src/shared/api/delivery.test.ts`
- Create: `platform/apps/admin-web/src/shared/api/responses.ts`
- Create: `platform/apps/admin-web/src/shared/api/responses.test.ts`
- Create: `platform/apps/admin-web/src/shared/api/exports.ts`
- Create: `platform/apps/admin-web/src/shared/api/exports.test.ts`
- Modify: `platform/apps/admin-web/src/test/server.ts`

- [ ] **Step 1: Write failing Zod and request-shape tests**

覆盖 `LinkView`、`ResponseSummary`、`ResponsePage/ResponseRow`、`ExportJobView`、查询参数编码、二维码 blob 和导出下载响应。

- [ ] **Step 2: Verify failure**

Run: `cd platform/apps/admin-web && npm test -- --run src/shared/api/delivery.test.ts src/shared/api/responses.test.ts src/shared/api/exports.test.ts`

- [ ] **Step 3: Implement typed clients and tenant-scoped query keys**

查询键必须包含 tenant/survey/filter/cursor；下载不经 JSON parser，但必须校验 HTTP 状态与文件名。

- [ ] **Step 4: Verify and commit**

Run: `cd platform/apps/admin-web && npm test -- --run src/shared/api && npm run typecheck && npm run lint`

```bash
git add platform/apps/admin-web/src/shared/api platform/apps/admin-web/src/test/server.ts
git commit -m "feat: add delivery response and export clients"
```

### Task 2: Published access panel and data workspace

**Files:**
- Create: `platform/apps/admin-web/src/features/publish/PublishedAccessPanel.tsx`
- Create: `platform/apps/admin-web/src/features/publish/PublishedAccessPanel.test.tsx`
- Modify: `platform/apps/admin-web/src/features/publish/PublishPage.tsx`
- Modify: `platform/apps/admin-web/src/features/publish/PublishPage.test.tsx`
- Create: `platform/apps/admin-web/src/features/responses/ResponsesPage.tsx`
- Create: `platform/apps/admin-web/src/features/responses/ResponsesPage.test.tsx`
- Create: `platform/apps/admin-web/src/features/responses/responses.css`
- Modify: `platform/apps/admin-web/src/app/router.tsx`
- Modify: `platform/apps/admin-web/src/app/SurveyShell.tsx`
- Modify: `platform/apps/admin-web/src/app/SurveyShell.test.tsx`

- [ ] **Step 1: Write failing workflow component tests**

发布成功后显示创建/复制/撤销链接、二维码、答卷摘要和「答卷与导出」入口；未发布、无权限、无答卷和加载失败有独立状态。

- [ ] **Step 2: Implement publish aftercare UI**

不再把版本列表作为发布终点；链接操作在 mutation 后精确失效 delivery/summary 查询。

- [ ] **Step 3: Implement response list and export jobs**

先交付摘要、状态/版本筛选、明细分页、格式选择、任务进度、取消、下载和过期状态；只显示后端真实支持格式。

- [ ] **Step 4: Verify responsive and accessibility behavior**

Run: `cd platform/apps/admin-web && npm test -- --run src/features/publish src/features/responses src/app/SurveyShell.test.tsx && npm run typecheck && npm run lint`

- [ ] **Step 5: Commit**

```bash
git add platform/apps/admin-web/src/features platform/apps/admin-web/src/app
git commit -m "feat: add publish aftercare and response workspace"
```

### Task 3: Isolated preview backend contract

**Files:**
- Create: `platform/services/business/src/main/resources/db/migration/V550__survey_preview_session.sql`
- Create: `platform/services/business/src/main/java/cn/mjy/platform/survey/preview/PreviewSessionController.java`
- Create: `platform/services/business/src/main/java/cn/mjy/platform/survey/preview/PreviewSessionService.java`
- Create: `platform/services/business/src/main/java/cn/mjy/platform/survey/preview/PreviewSessionRepository.java`
- Create: `platform/services/business/src/main/java/cn/mjy/platform/survey/preview/PreviewSessionView.java`
- Create: `platform/services/business/src/test/java/cn/mjy/platform/survey/preview/PreviewSessionApiTest.java`
- Modify: `platform/contracts/publish-gateway-v1.md`
- Modify: `platform/tools/publish-gateway/pubgw/server.py`
- Modify: `platform/tools/publish-gateway/pubgw/service.py`
- Create: `platform/tools/publish-gateway/tests/test_preview.py`

**Interfaces:** `POST /v1/surveys/{id}/preview-sessions`, `GET /v1/preview-sessions/{id}`, `DELETE /v1/preview-sessions/{id}`; Gateway internal `POST /v1/preview` and idempotent close.

- [ ] **Step 1: Write failing tenant, idempotency and isolation tests**

断言草稿版本固定、重复 request ID 返回同一 session、预览 SID 不进入 `published_version`、预览作答不进入 response projection，跨租户不可见。

- [ ] **Step 2: Implement gateway preview operation**

复用现有 compiler/publish stages，但绑定 preview generation 和 TTL metadata；不更改正式 route，不触发发布版本结算。

- [ ] **Step 3: Implement platform session lifecycle and cleanup**

保存状态 `creating|ready|closing|closed|failed|cleanup_failed`，返回短期签名 URL；定时回收到期 session，失败保留审计与重试计数。

- [ ] **Step 4: Verify backend and gateway tests**

Run: `platform/deploy/platform-dev/mvn.sh -Dtest='cn.mjy.platform.survey.preview.*Test' test`

Run: `python3 -m unittest platform/tools/publish-gateway/tests/test_preview.py platform/tools/publish-gateway/tests/test_publish.py`

- [ ] **Step 5: Commit**

```bash
git add platform/services/business platform/contracts/publish-gateway-v1.md platform/tools/publish-gateway
git commit -m "feat: add isolated runtime preview sessions"
```

### Task 4: Real preview UI and full product journey

**Files:**
- Create: `platform/apps/admin-web/src/shared/api/previews.ts`
- Create: `platform/apps/admin-web/src/shared/api/previews.test.ts`
- Modify: `platform/apps/admin-web/src/features/preview/PreviewPage.tsx`
- Modify: `platform/apps/admin-web/src/features/preview/PreviewPage.test.tsx`
- Modify: `platform/apps/admin-web/e2e/authoring.spec.ts`
- Modify: `platform/deploy/test/run-admin-web-e2e.sh`
- Modify: `platform/docs/traceability/requirement-tests.md`
- Modify: `platform/docs/productization/capabilities.json`

- [ ] **Step 1: Write failing UI and browser tests**

快速预览保留用于低成本草稿检查；「真实预览」创建 session、展示过期时间、打开新窗口并允许显式结束。

- [ ] **Step 2: Implement preview UI**

不使用 iframe 绕过安全策略；创建中禁止重复提交，失败保留可重试操作和 request ID 摘要。

- [ ] **Step 3: Add real browser journey**

创建问卷 -> 隔离预览作答 -> 确认正式答卷计数不变 -> 发布 -> 建链接/二维码 -> 正式作答 -> 答卷页可见 -> 创建并下载导出。

- [ ] **Step 4: Run full gates and update evidence**

Run: `platform/deploy/test/run-admin-web-e2e.sh`

Run: `platform/deploy/test/run-p1-e2e.sh`

Run: `python3 platform/tools/productization/render_capabilities.py --check`

- [ ] **Step 5: Commit**

```bash
git add platform/apps/admin-web platform/deploy/test platform/docs
git commit -m "feat: complete publish to response product journey"
```
