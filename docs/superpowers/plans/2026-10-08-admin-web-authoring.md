# 问卷作者工作台首期 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付一个通过真实平台、发布网关和测试引擎完成“登录、创建、编辑/导入、审批、发布、查看版本”的中文问卷作者工作台。

**Architecture:** 在 `platform/apps/admin-web/` 新建同源部署的 React SPA，业务 API 仍由现有 Spring Boot 平台提供。前端以显式 API 适配层和无损 `SurveyDefinition` 适配层隔离后端契约；组织免登新增短时单次 `handoff`，只在交换成功时创建平台会话并签发 JWT。

**Tech Stack:** Java 21、Spring Boot 3、PostgreSQL 16、React 19、TypeScript、Vite、React Router、TanStack Query、React Hook Form、Zod、Vitest、Testing Library、MSW、Playwright、Docker、nginx

**Spec:** `docs/superpowers/specs/2026-10-08-admin-web-authoring-design.md`

## Global Constraints

- 所有租户与权限判定继续由平台从已验签 JWT 获取；前端不得发送或信任租户覆盖参数。
- 浏览器只访问同源 `/v1`；不得访问发布网关或 LimeSurvey 管理 API。
- JWT 不进入 URL、日志、`localStorage`、`sessionStorage` 或 IndexedDB；只保存在页面内存。
- 管理端登录 `handoff` 最长存活 60 秒、单次使用、绑定 HttpOnly 浏览器 Cookie；交换成功前不创建平台会话。
- 首期只可视编辑说明文字、单选、多选、短文本、长文本；复杂题型只读并无损往返。
- 本地预览必须显示“草稿预览”，不得宣称与 LimeSurvey 真实运行时一致。
- Node 使用 22；所有 npm 依赖精确锁定并提交 `package-lock.json`。
- 使用 `lucide-react` 图标；管理界面保持紧凑、中文优先、键盘可操作，不使用页面套卡片或装饰性渐变。
- Java 修改遵循 `platform/services/business/CONVENTIONS.md`；新身份迁移使用 V111，不修改已发布迁移。
- 每项实现先写失败测试、确认失败、再写最小实现；平台全套数字只认 `run-platform-tests.sh` 对账结果。
- 不修改 LimeSurvey 上游源码；不把秘密、JWT、口令或完整敏感响应写入测试快照、日志和文档。

## Review Focus

- 复杂题型携带未知嵌套字段时，基础编辑与保存必须逐字段保留未拥有内容；Task 4 的无损往返测试覆盖。
- 两名作者从同一 `draftVersion` 编辑时，后提交者必须保留本地内容并明确冲突，不得自动覆盖；Task 4 的冲突页面测试覆盖。
- 登录 `handoff` 被重放、过期、换浏览器或跨租户使用时必须统一拒绝且不创建会话；Task 1 的集成测试覆盖。
- 发布返回 `202` 后经历多次轮询、最终失败或转人工复核时，界面不得提前显示成功；Task 6 的状态机测试覆盖。
- 401 在有未保存内容时不得静默丢稿；Task 2 与 Task 4 的会话失效及恢复测试覆盖。

---

### Task 1: 浏览器组织免登交接

**Files:**
- Create: `platform/services/business/src/main/resources/db/migration/V111__org_login_handoff.sql`
- Create: `platform/services/business/src/main/java/cn/mjy/platform/identity/org/OrgLoginHandoffRepository.java`
- Create: `platform/services/business/src/main/java/cn/mjy/platform/identity/org/OrgLoginHandoffService.java`
- Modify: `platform/services/business/src/main/java/cn/mjy/platform/identity/org/OrgLoginController.java`
- Modify: `platform/services/business/src/main/java/cn/mjy/platform/identity/org/OrgLoginService.java`
- Modify: `platform/services/business/src/main/java/cn/mjy/platform/identity/org/OrgLoginStateRepository.java`
- Modify: `platform/services/business/src/main/java/cn/mjy/platform/identity/org/OrgLoginProperties.java`
- Create: `platform/services/business/src/test/java/cn/mjy/platform/identity/org/OrgLoginHandoffTest.java`
- Modify: `platform/services/business/src/test/java/cn/mjy/platform/identity/org/OrgLoginTestSupport.java`
- Modify: `platform/services/business/src/test/java/cn/mjy/platform/identity/org/OrgTablesIsolationTest.java`

**Interfaces:**
- Consumes: 现有 `OrgLoginService` 的提供方认证、`PlatformTokenIssuer` 和可撤销组织会话。
- Produces: `GET /v1/auth/org/{tenantId}/{connectionId}/start?client=admin_web`；提供方回调后 302 到固定管理端 `/auth/callback?tenant=<tenantId>&handoff=<opaque>`；`POST /v1/auth/org/{tenantId}/handoff` 接收 `ExchangeHandoff(String handoff)`，返回现有 `TokenView`。
- Produces: `OrgLoginService.Authenticated(TenantId tenantId, UUID principalId, UUID connectionId, OrgProvider provider)`；`OrgLoginService.issue(Authenticated) -> SignedIn`。

- [ ] **Step 1: 写登录交接失败测试**

在 `OrgLoginHandoffTest` 添加下列测试：

```java
void adminWebCallbackRedirectsWithoutPuttingTheJwtInTheUrl()
void exchangingTheHandoffCreatesTheSessionAndReturnsTheJwt()
void aHandoffCanOnlyBeUsedOnce()
void anExpiredHandoffIsRejectedWithoutCreatingASession()
void aHandoffFromAnotherBrowserIsRejected()
void aHandoffCannotBeReadFromAnotherTenantScope()
void anUnconfiguredAdminWebRedirectIsRejectedAtStart()
```

断言重定向 URL 只含租户标识和随机 `handoff`，不含 JWT 或主体；交换前 `org_session` 无新增行，交换成功后恰好新增一行。把相同 `handoff` 放到另一租户路径必须查不到。

- [ ] **Step 2: 运行目标测试并确认失败**

Run: `PLATFORM_DB_NAME=platform_admin_auth platform/deploy/test/run-platform-tests.sh -- -Dtest=OrgLoginHandoffTest`

Expected: FAIL，原因是 V111、交接端点和服务尚不存在；不得出现“0 tests”。

- [ ] **Step 3: 添加 V111 表与最小仓储**

创建 `org_login_handoff`：`handoff_hash` 主键、`browser_hash`、`tenant_id`、`principal_id`、`connection_id`、`provider`、`expires_at`、`consumed_at`。运行期账号只允许插入、原子消费和清理；表启用并强制 RLS，策略按 `tenant_id = app_current_tenant()`。

`OrgLoginHandoffRepository` 提供：

```java
void insert(TenantId tenant, String handoffHash, String browserHash,
            UUID principalId, UUID connectionId, OrgProvider provider, Instant expiresAt)
Optional<PendingHandoff> consume(TenantId tenant, String handoffHash, Instant now)
int purgeStale(TenantId tenant, Instant now)
```

- [ ] **Step 4: 拆分认证与会话签发**

把现有 `complete(...)` 内部步骤拆为：

```java
Authenticated authenticate(TenantId tenant, UUID connectionId, String code,
                           String state, String browserNonce)
SignedIn issue(Authenticated authenticated, String trace)
```

原 JSON 回调仍按 `authenticate -> issue` 工作，保持向后兼容；`admin_web` 回调只 `authenticate -> handoffs.create`。

- [ ] **Step 5: 实现创建、重定向和单次交换**

`OrgLoginHandoffService` 提供：

```java
CreatedHandoff create(Authenticated authenticated)
SignedIn exchange(TenantId tenant, String opaqueHandoff, String browserNonce)
```

随机值至少 32 字节；数据库只存 SHA-256；TTL 固定 60 秒。新增配置 `platform.identity.org-login.admin-web-base-url`，只接受一个固定 HTTPS 基础 URL（测试环境允许 `http://127.0.0.1`）。交接 Cookie 为 HttpOnly、SameSite=Strict、Secure、仅交换路径可见。

- [ ] **Step 6: 运行目标测试并确认通过**

Run: `PLATFORM_DB_NAME=platform_admin_auth platform/deploy/test/run-platform-tests.sh -- -Dtest=OrgLoginFlowTest,OrgLoginHandoffTest,OrgTablesIsolationTest`

Expected: PASS，旧 JSON 登录流程仍通过，新增交接拒绝分支全部通过。

- [ ] **Step 7: 提交认证交接**

```bash
git add platform/services/business/src/main/resources/db/migration/V111__org_login_handoff.sql \
  platform/services/business/src/main/java/cn/mjy/platform/identity/org \
  platform/services/business/src/test/java/cn/mjy/platform/identity/org
git commit -m "feat: add browser-safe admin login handoff"
```

### Task 2: 管理端工程基线、会话与 API 层

**Files:**
- Create: `platform/apps/admin-web/package.json`
- Create: `platform/apps/admin-web/package-lock.json`
- Create: `platform/apps/admin-web/tsconfig.json`
- Create: `platform/apps/admin-web/vite.config.ts`
- Create: `platform/apps/admin-web/vitest.config.ts`
- Create: `platform/apps/admin-web/eslint.config.js`
- Create: `platform/apps/admin-web/index.html`
- Create: `platform/apps/admin-web/src/main.tsx`
- Create: `platform/apps/admin-web/src/app/App.tsx`
- Create: `platform/apps/admin-web/src/app/router.tsx`
- Create: `platform/apps/admin-web/src/app/styles.css`
- Create: `platform/apps/admin-web/src/features/auth/AuthProvider.tsx`
- Create: `platform/apps/admin-web/src/features/auth/AuthCallbackPage.tsx`
- Create: `platform/apps/admin-web/src/features/auth/DevTokenPage.tsx`
- Create: `platform/apps/admin-web/src/shared/api/http.ts`
- Create: `platform/apps/admin-web/src/shared/api/schemas.ts`
- Create: `platform/apps/admin-web/src/shared/api/errors.ts`
- Create: `platform/apps/admin-web/src/shared/api/me.ts`
- Create: `platform/apps/admin-web/src/test/server.ts`
- Create: `platform/apps/admin-web/src/test/render.tsx`
- Create: `platform/apps/admin-web/src/features/auth/AuthProvider.test.tsx`
- Create: `platform/apps/admin-web/src/features/auth/AuthCallbackPage.test.tsx`

**Interfaces:**
- Consumes: Task 1 `POST /v1/auth/org/{tenantId}/handoff` 与现有 `GET /v1/me`、`POST /v1/auth/logout`。
- Produces: `ApiClient.request<T>(request: ApiRequest<T>): Promise<T>`；`AuthSession { token, me, expiresAt }` 只驻留内存；受保护路由和统一中文 `ApiErrorKind`。

- [ ] **Step 1: 初始化锁定依赖的前端工程**

用 Node 22 创建 Vite React TypeScript 工程；以 `--save-exact` 安装 React、Router、TanStack Query、React Hook Form、Zod、lucide-react、Vitest、Testing Library、MSW、Playwright、ESLint。提交 npm lock 文件，不使用 `latest` 范围。

- [ ] **Step 2: 写会话与 API 错误失败测试**

测试名称：

```text
keepsTheJwtOnlyInTheProviderMemory
exchangesTheHandoffAndLoadsMe
clearsTheSessionOn401ButPreservesTheEditorRecoveryPayload
maps403404409422And202ToChineseDomainStates
doesNotRenderTheDevTokenEntryInAProductionBuild
```

- [ ] **Step 3: 运行前端测试并确认失败**

Run: `npm test -- --run src/features/auth/AuthProvider.test.tsx src/features/auth/AuthCallbackPage.test.tsx`

Workdir: `platform/apps/admin-web`

Expected: FAIL，缺少 provider、回调页面与 API 客户端。

- [ ] **Step 4: 实现应用框架与内存会话**

实现 `AuthProvider`、受保护路由、`/auth/callback` 和仅开发构建可达的 `/dev/token`。401 清会话前调用注入的 `beforeSessionClear`，让编辑器把当前内存草稿移入同页恢复槽；不得写浏览器持久化存储。

- [ ] **Step 5: 实现类型化 API 与运行时校验**

`request` 统一设置 Bearer 头、JSON、超时和 AbortSignal；Zod 校验响应。错误归一化为：`unauthenticated`、`forbidden`、`not_found`、`conflict`、`validation`、`pending`、`unavailable`、`unexpected`。

- [ ] **Step 6: 运行质量命令并确认通过**

Run: `npm run lint`

Run: `npm run typecheck`

Run: `npm test -- --run`

Run: `npm run build`

Expected: 全部退出 0；生产构建中不包含开发令牌输入路由。

- [ ] **Step 7: 提交前端基线**

```bash
git add platform/apps/admin-web
git commit -m "feat: bootstrap the survey admin web app"
```

### Task 3: 工作区与资源树

**Files:**
- Create: `platform/apps/admin-web/src/shared/api/resources.ts`
- Create: `platform/apps/admin-web/src/features/workspace/WorkspacePage.tsx`
- Create: `platform/apps/admin-web/src/features/workspace/ResourceTree.tsx`
- Create: `platform/apps/admin-web/src/features/workspace/CreateResourceDialog.tsx`
- Create: `platform/apps/admin-web/src/features/workspace/workspace.css`
- Create: `platform/apps/admin-web/src/features/workspace/WorkspacePage.test.tsx`
- Create: `platform/apps/admin-web/src/shared/api/surveys.ts`
- Modify: `platform/apps/admin-web/src/app/router.tsx`

**Interfaces:**
- Consumes: `GET /v1/resources`、`POST /v1/projects`、`POST /v1/folders`、`POST /v1/surveys`。
- Produces: 可分页展开的资源树；新问卷定义工厂 `createBlankDefinition(title: string): Record<string, unknown>`，生成 definition v2、`zh-Hans`、一个题组和一条说明文字。Task 4 的适配层接管后再把它解析为编辑模型。

- [ ] **Step 1: 写资源工作区失败测试**

```text
loadsOnlyTheSelectedBranchAndFollowsOpaqueCursors
restoresTheSelectedResourceFromTheUrl
createsAProjectFolderAndBlankSurveyWithChineseDefaults
keepsForbiddenActionsOutOfTheTabOrder
shows404AsUnavailableWithoutRevealingAnotherTenant
```

- [ ] **Step 2: 运行目标测试并确认失败**

Run: `npm test -- --run src/features/workspace/WorkspacePage.test.tsx`

Expected: FAIL，页面与 API 模块不存在。

- [ ] **Step 3: 实现资源 API 与分页树**

类型严格对应 `ResourceView` 和 `ResourcePage`。只在展开节点时请求子级；`nextCursor` 原样回传，不解析。URL 查询参数保存当前 `resource`。

- [ ] **Step 4: 实现创建操作与空白定义**

项目和文件夹使用简洁对话框。问卷创建要求父级、标题，并由 `createBlankDefinition` 生成合法完整定义；创建成功后进入编辑页。

- [ ] **Step 5: 运行测试和构建**

Run: `npm test -- --run src/features/workspace/WorkspacePage.test.tsx`

Run: `npm run typecheck`

Expected: PASS。

- [ ] **Step 6: 提交工作区**

```bash
git add platform/apps/admin-web/src/features/workspace platform/apps/admin-web/src/shared/api/resources.ts \
  platform/apps/admin-web/src/shared/api/surveys.ts platform/apps/admin-web/src/app/router.tsx
git commit -m "feat: add the author workspace resource tree"
```

### Task 4: 无损问卷定义模型与基础编辑器

**Files:**
- Modify: `platform/apps/admin-web/src/shared/api/surveys.ts`
- Create: `platform/apps/admin-web/src/features/editor/model/definition.ts`
- Create: `platform/apps/admin-web/src/features/editor/model/operations.ts`
- Create: `platform/apps/admin-web/src/features/editor/model/definition.test.ts`
- Create: `platform/apps/admin-web/src/test/fixtures/publish-gateway.json`
- Create: `platform/apps/admin-web/src/features/editor/EditorPage.tsx`
- Create: `platform/apps/admin-web/src/features/editor/Outline.tsx`
- Create: `platform/apps/admin-web/src/features/editor/QuestionEditor.tsx`
- Create: `platform/apps/admin-web/src/features/editor/PropertiesPanel.tsx`
- Create: `platform/apps/admin-web/src/features/editor/ReadOnlyQuestion.tsx`
- Create: `platform/apps/admin-web/src/features/editor/editor.css`
- Create: `platform/apps/admin-web/src/features/editor/EditorPage.test.tsx`
- Modify: `platform/apps/admin-web/src/app/router.tsx`

**Interfaces:**
- Consumes: `GET /v1/surveys/{id}`、`GET /v1/surveys/{id}/draft`、`PUT /v1/surveys/{id}/draft`。
- Produces: `parseDefinition(unknown) -> EditableSurveyDefinition`；`serializeDefinition(EditableSurveyDefinition) -> unknown`；以 UUID 操作题组/题目和排序；`saveDraft(id, expectedVersion, definition)`。

- [ ] **Step 1: 写无损定义失败测试**

用真实 fixture `platform/services/business/src/test/resources/surveys/publish-gateway.json` 的前端副本，加上未知顶层字段、未知题目字段和复杂扩展字段。断言：

```text
roundTripsEveryFieldTheEditorDoesNotOwn
editsOnlyTheSelectedBasicQuestion
keepsQuestionAndGroupUuidsStableWhileReordering
rejectsDuplicateCodesAndBlankOptionsBeforeSave
createsStableDefaultsForXLSMAndTQuestionTypes
```

- [ ] **Step 2: 运行模型测试并确认失败**

Run: `npm test -- --run src/features/editor/model/definition.test.ts`

Expected: FAIL，模型与操作不存在。

- [ ] **Step 3: 实现无损适配层**

内部节点同时保留 `source` 原对象和首期拥有字段。序列化时从 `source` 克隆后只覆盖拥有字段。支持类型：`X`、`L`、`M`、`S`、`T`；其他类型走 `ReadOnlyQuestion`，不允许改变类型。

- [ ] **Step 4: 写编辑页面与冲突失败测试**

```text
keepsTheSelectedQuestionAcrossRouteChanges
warnsBeforeLeavingWithUnsavedChanges
savesWithTheCurrentDraftVersionAndAdoptsTheReturnedVersion
keepsLocalChangesWhenTheServerReturns409
recoversTheInMemoryDraftAfterReauthentication
switchesOutlineEditorAndPropertiesAsTabsOnNarrowScreens
```

- [ ] **Step 5: 运行页面测试并确认失败**

Run: `npm test -- --run src/features/editor/EditorPage.test.tsx`

Expected: FAIL，编辑页面尚未实现。

- [ ] **Step 6: 实现三栏编辑器、显式保存与冲突对话框**

桌面三栏使用稳定 grid track；窄屏使用 tabs。保存按钮带保存图标和可访问名称。409 后禁止自动覆盖，提供“重新载入”和“导出本地草稿”操作；导出文件只在用户主动点击时生成，不自动写磁盘。

- [ ] **Step 7: 运行模型、页面与构建检查**

Run: `npm test -- --run src/features/editor`

Run: `npm run lint`

Run: `npm run typecheck`

Run: `npm run build`

Expected: 全部通过。

- [ ] **Step 8: 提交基础编辑器**

```bash
git add platform/apps/admin-web/src/features/editor platform/apps/admin-web/src/shared/api/surveys.ts \
  platform/apps/admin-web/src/test/fixtures/publish-gateway.json platform/apps/admin-web/src/app/router.tsx
git commit -m "feat: add lossless basic survey editing"
```

### Task 5: 文本导入与草稿预览

**Files:**
- Create: `platform/apps/admin-web/src/shared/api/imports.ts`
- Create: `platform/apps/admin-web/src/features/import/ImportPage.tsx`
- Create: `platform/apps/admin-web/src/features/import/ImportPreviewTable.tsx`
- Create: `platform/apps/admin-web/src/features/import/ImportPage.test.tsx`
- Create: `platform/apps/admin-web/src/features/preview/PreviewPage.tsx`
- Create: `platform/apps/admin-web/src/features/preview/DraftRenderer.tsx`
- Create: `platform/apps/admin-web/src/features/preview/preview.css`
- Create: `platform/apps/admin-web/src/features/preview/PreviewPage.test.tsx`
- Modify: `platform/apps/admin-web/src/app/router.tsx`

**Interfaces:**
- Consumes: `POST /v1/surveys/{id}/import/preview`、`POST /v1/surveys/{id}/import`、Task 4 草稿查询和定义模型。
- Produces: 合法题目选择、坏行定位、导入后草稿替换；只读的桌面/移动草稿渲染。

- [ ] **Step 1: 写导入和预览失败测试**

```text
previewsWithoutWritingAndSelectsValidQuestionsByDefault
keepsBadLinesVisibleWithTheirOriginalLineNumbers
importsOnlyTheCheckedOrdinalsIntoTheChosenGroup
preservesTheLocalTextWhenImportConflicts
labelsTheRendererAsDraftPreview
rendersUnsupportedQuestionsAsReadablePlaceholders
```

- [ ] **Step 2: 运行目标测试并确认失败**

Run: `npm test -- --run src/features/import src/features/preview`

Expected: FAIL。

- [ ] **Step 3: 实现导入预览与确认**

预览请求不修改编辑状态；确认导入携带当前 `expectedVersion` 和服务端返回序号。422 问题按行号显示；409 保留原文和勾选状态。

- [ ] **Step 4: 实现草稿预览**

复用 Task 4 定义模型，只渲染首期基础题型；不发送答案、不调用引擎。桌面/移动用分段控件切换固定预览宽度，页面始终显示“草稿预览”。

- [ ] **Step 5: 运行测试、类型检查和构建**

Run: `npm test -- --run src/features/import src/features/preview`

Run: `npm run typecheck`

Run: `npm run build`

Expected: PASS。

- [ ] **Step 6: 提交导入与预览**

```bash
git add platform/apps/admin-web/src/features/import platform/apps/admin-web/src/features/preview \
  platform/apps/admin-web/src/shared/api/imports.ts platform/apps/admin-web/src/app/router.tsx
git commit -m "feat: add survey import and draft preview"
```

### Task 6: 审批、发布状态与版本记录

**Files:**
- Create: `platform/apps/admin-web/src/shared/api/approvals.ts`
- Create: `platform/apps/admin-web/src/features/publish/PublishPage.tsx`
- Create: `platform/apps/admin-web/src/features/publish/ApprovalTimeline.tsx`
- Create: `platform/apps/admin-web/src/features/publish/PublishStatus.tsx`
- Create: `platform/apps/admin-web/src/features/publish/VersionDetailPage.tsx`
- Create: `platform/apps/admin-web/src/features/publish/publish.css`
- Create: `platform/apps/admin-web/src/features/publish/PublishPage.test.tsx`
- Modify: `platform/apps/admin-web/src/app/router.tsx`

**Interfaces:**
- Consumes: 现有 approval、publish、survey、versions API。
- Produces: 审批状态机 UI；`202` 有界轮询；失败阶段、人工复核和孤儿 sid 告警；已发布版本只读详情。

- [ ] **Step 1: 写审批与发布状态失败测试**

```text
showsOnlyActionsAllowedByTheCurrentApprovalState
refreshesAnApprovedRequestToVoidedAfterTheDraftChanges
doesNotClaimSuccessWhilePublishReturns202
stopsPollingWhenTheSurveyBecomesPublished
showsManualReviewAndOrphanSidAsBlockingWarnings
renders422FailuresAtThePublishBoundary
neverRendersRawGatewaySecretsOrStackTraces
showsPublishedVersionsAsImmutableReadOnlyData
```

- [ ] **Step 2: 运行目标测试并确认失败**

Run: `npm test -- --run src/features/publish/PublishPage.test.tsx`

Expected: FAIL。

- [ ] **Step 3: 实现审批时间线与动作**

中文映射六种审批状态。动作成功后同时失效问卷概览、申请列表和版本列表；`403` 仍显示服务端拒绝，不把客户端角色判断当最终授权。

- [ ] **Step 4: 实现发布轮询与版本详情**

`202` 使用 TanStack Query 每 2 秒刷新问卷概览；页面不可见时暂停，到 `published`、`publish_failed` 或 `manualReviewAt != null` 时停止。页面卸载时取消轮询。版本详情只读显示绑定、发布时间、live 状态和题目字段映射。

- [ ] **Step 5: 运行测试、类型检查和构建**

Run: `npm test -- --run src/features/publish`

Run: `npm run typecheck`

Run: `npm run build`

Expected: PASS。

- [ ] **Step 6: 提交发布工作区**

```bash
git add platform/apps/admin-web/src/features/publish platform/apps/admin-web/src/shared/api/approvals.ts \
  platform/apps/admin-web/src/app/router.tsx
git commit -m "feat: add approval and publish tracking"
```

### Task 7: 同源容器与真实 Playwright 纵向验收

**Files:**
- Create: `platform/apps/admin-web/Dockerfile`
- Create: `platform/apps/admin-web/nginx.conf`
- Create: `platform/apps/admin-web/playwright.config.ts`
- Create: `platform/apps/admin-web/e2e/authoring.spec.ts`
- Create: `platform/deploy/test/admin-web-e2e.compose.yml`
- Create: `platform/deploy/test/run-admin-web-e2e.sh`
- Create: `platform/tests/e2e/admin_web_gate.py`
- Modify: `platform/apps/admin-web/package.json`

**Interfaces:**
- Consumes: Task 1–6 应用、现有 P1 测试栈与发布 fixture。
- Produces: 同源管理端容器；`platform/deploy/test/run-admin-web-e2e.sh` 一条命令启动真实平台、PostgreSQL、网关、引擎和浏览器验收。

- [ ] **Step 1: 写 Playwright 主路径测试**

测试完成：输入仅测试构建可见的短时测试 JWT、确认 `/v1/me`、创建项目/文件夹/问卷、编辑与保存、导入预览、提交并批准、发布、查看版本。断言最终通过平台 API 和引擎状态双重确认，不只检查页面文字。

- [ ] **Step 2: 运行 Playwright 并确认失败**

Run: `npm run e2e -- --project=chromium-desktop`

Workdir: `platform/apps/admin-web`

Expected: FAIL，因为同源容器和测试栈尚未接线。

- [ ] **Step 3: 实现生产构建镜像与 nginx 同源代理**

多阶段镜像只把 `dist/` 带入运行层。nginx 提供 SPA fallback、静态缓存、安全响应头，并把 `/v1` 与 `/actuator/health` 代理到平台；不得代理网关管理端点。

- [ ] **Step 4: 扩展 P1 栈并提供测试身份**

`admin_web_gate.py` 复用 P1 的开通、引擎实例和测试 JWT 生成逻辑，将 JWT 写入权限为 0600 的临时文件供 Playwright 读取；脚本退出时删除。不得把 JWT 打印到 stdout、容器日志或报告附件。

- [ ] **Step 5: 实现桌面和移动 Playwright 项目**

桌面验证完整主路径；移动项目验证编辑器 tabs、文本不重叠、主要动作可达和焦点可见。失败时只保留截图、trace 和脱敏网络元数据。

- [ ] **Step 6: 运行真实纵向验收**

Run: `SURVEY_TEST_PREFIX=adminweb COMPOSE_PROJECT_NAME=adminweb TEST_DB=mysql platform/deploy/test/run-admin-web-e2e.sh --fresh`

Expected: PASS；平台、网关、引擎和浏览器断言全绿，脚本自动清理自己的容器、网络、数据库和临时 JWT 文件。

- [ ] **Step 7: 提交容器与端到端验收**

```bash
git add platform/apps/admin-web/Dockerfile platform/apps/admin-web/nginx.conf \
  platform/apps/admin-web/playwright.config.ts platform/apps/admin-web/e2e \
  platform/deploy/test/admin-web-e2e.compose.yml platform/deploy/test/run-admin-web-e2e.sh \
  platform/tests/e2e/admin_web_gate.py platform/apps/admin-web/package.json
git commit -m "test: verify authoring through the real platform stack"
```

### Task 8: CI、追溯和交付文档

**Files:**
- Modify: `.github/workflows/platform-quality.yml`
- Modify: `platform/README.md`
- Modify: `platform/docs/p2/progress.md`
- Modify: `platform/docs/traceability/requirement-tests.md`
- Modify: `/Users/lionel/Documents/MJY 2/LimeSurvey本土化方案/04-开发方案总蓝图（仓库校准版）.md`
- Modify: `/Users/lionel/Documents/MJY 2/LimeSurvey本土化方案/08-需求进度与方案修订（仓库实证）.md`

**Interfaces:**
- Consumes: Task 1–7 的实际代码、测试结果和测试数量。
- Produces: 管理端 CI job、准确的仓库状态、需求↔测试证据与蓝图修订；不提前宣称未完成断言为 Accepted。

- [ ] **Step 1: 先运行最终验证并记录实际结果**

Run: `PLATFORM_DB_NAME=platform_admin_final platform/deploy/test/run-platform-tests.sh`

Run: `cd platform/tools/publish-gateway && python3 -m unittest discover -s tests -t . -q`

Run: `cd platform/tools/traceability && python3 -m unittest discover -s tests -t . -q && python3 -m reqtrace.cli check`

Run: `cd platform/apps/admin-web && npm run lint && npm run typecheck && npm test -- --run && npm run build`

Run: `SURVEY_TEST_PREFIX=adminweb-final COMPOSE_PROJECT_NAME=adminweb-final TEST_DB=mysql platform/deploy/test/run-admin-web-e2e.sh --fresh`

Expected: 全部退出 0；记录实际平台测试数、前端测试数和 Playwright 场景数。

- [ ] **Step 2: 把管理端检查接入 CI**

增加轻量 `admin-web` job：setup-node 22、`npm ci`、lint、typecheck、Vitest、build。真实 Playwright job 复用 `run-admin-web-e2e.sh`，依赖轻量 job 和 `parity-tables`；失败时上传脱敏截图与 trace，不上传 JWT 临时文件。

- [ ] **Step 3: 更新仓库状态与需求追溯**

`platform/README.md` 更新真实 HEAD、目录、运行命令、系统构成、测试基线和管理端能力。P2 进度记录首期范围、证据、仍未支持的题型与真实预览缺口。追溯表只登记测试确实覆盖的 R01、R19、R23 子断言。

- [ ] **Step 4: 更新仓库外蓝图实证**

修正“没有管理端前端”的旧事实，写明首期实际范围；保留未满足的拖拽、高级题型、真实运行时预览、读屏专项等断言。不要把工程切片完成等同于整条需求 Accepted。

- [ ] **Step 5: 运行文档与仓库检查**

Run: `git diff --check`

Run: `cd platform/tools/traceability && python3 -m reqtrace.cli check`

Expected: 无格式错误；追溯校验通过。

- [ ] **Step 6: 提交 CI 与仓库内文档**

```bash
git add .github/workflows/platform-quality.yml platform/README.md platform/docs/p2/progress.md \
  platform/docs/traceability/requirement-tests.md
git commit -m "docs: record the admin authoring delivery evidence"
```

仓库外蓝图不加入 Git 提交；在最终推送完成后按 AGENTS 约定更新 Obsidian 项目总览或路线图，记录日期、分支、提交、测试、遗留和下一步。

### Task 9: 完成前复核、推送与三方同步

**Files:**
- Review: 当前分支全部变更
- Modify after push: `/Users/lionel/Library/Favorites/AI/claude/10-项目/问卷调查与本地化平台（Survey · LimeSurvey）/项目总览 - 问卷调查与本地化平台（Survey · LimeSurvey）.md` 或对应进度路线图

**Interfaces:**
- Consumes: 全部分阶段提交与最终验证证据。
- Produces: 可审查分支、正确 GitHub 推送和 Obsidian 可追溯记录。

- [ ] **Step 1: 复核改动边界**

Run: `git status --short --branch`

Run: `git diff --stat main...HEAD`

Run: `git log --oneline main..HEAD`

Expected: 只包含作者工作台、认证交接、测试和相关文档；不得出现当前 main 新能力的大面积删除。

- [ ] **Step 2: 使用 verification-before-completion 重新核对关键命令**

不得引用早先输出代替最终验证。至少重新运行 Task 8 Step 1 的平台全套、前端全套和真实纵向验收，并检查最新退出码。

- [ ] **Step 3: 请求全分支代码审查**

使用 `superpowers:requesting-code-review`；修复审查发现后重新运行受影响验证。

- [ ] **Step 4: 按 finishing-a-development-branch 完成分支**

确认 `origin` 仍为 `https://github.com/lsgoodlionel/star-survey.git`、目标分支未发生需要合并或变基的变化。遇到远端前进、保护分支、认证失败、测试失败或无法安全拆分的改动时停止并报告。

- [ ] **Step 5: 普通推送当前功能分支**

Run: `git push -u origin feat/admin-web-authoring`

Expected: 成功创建/更新远端功能分支；禁止 force push，禁止推到 `limesurvey-fork`。

- [ ] **Step 6: 更新 Obsidian 项目记录**

记录 2026-10-08、分支、提交、GitHub 关系、平台/前端/Playwright 测试证据、已交付范围、未完成断言和下一步。不得记录令牌、密钥、Cookie 或敏感配置值。
