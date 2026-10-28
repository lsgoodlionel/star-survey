# Admin Product Alignment Phase A+B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付独立、稳定的人工演示环境，并把管理端重构为具有全局导航、问卷上下文、业务排序、搜索过滤和资源治理能力的可用工作区。

**Architecture:** 平台业务服务负责授权后的筛选、稳定排序、复合游标和可恢复归档；React 管理端以 `AppShell`、`SurveyShell`、项目导航和资源列表分离四层导航语义。人工 demo 与 E2E 使用不同 Compose project、租户和持久化策略，LimeSurvey `/admin` 只作为本地运维入口。

**Tech Stack:** Java 21、Spring Boot 4.1、PostgreSQL 16/Flyway、React 19、TypeScript 5.9、React Router 7、TanStack Query 5、Vitest 5、Playwright 1.64、Docker Compose。

**Spec:** `docs/superpowers/specs/2026-10-09-admin-product-alignment-design.md`

## Global Constraints

- 平台管理端是作者、审核者和租户管理员的唯一业务入口；业务导航不得链接 LimeSurvey `/admin`。
- 本地/CI 保持 `force_ssl=off` 且设置 `ssl_disable_alert=true`；生产必须在真实 TLS 和代理头验证后使用 `force_ssl=on`。
- 默认资源顺序固定为项目/文件夹/问卷种类顺序、`updatedAt DESC`、规范化名称升序、UUID 升序。
- 搜索、状态过滤、排序和游标分页必须在服务端授权过滤后完成；前端不得只排序当前页。
- 归档可恢复且不得引入生产删除接口；归档容器使整棵子树从 active 视图消失。
- E2E 与人工 demo 使用不同租户；demo 固定资源为 `客户体验研究 / 2026 Q4 / 品牌跟踪调查`。
- 业务界面以中文为主，裸技术标识只能出现在折叠的技术信息区域。
- 所有桌面、平板和移动布局不得产生页面级横向溢出；触控目标至少 44×44px。
- 不回退 PR #10 已有的 pointer、键盘和按钮排序能力；本计划分支继续以其提交为基线。
- 不记录、提交或在终端输出令牌、密码和完整连接密钥；本地凭据只写入 mode `0600` 的忽略文件。

## Review Focus

- 旧游标与新筛选/排序组合混用时必须返回 400，不能悄悄重头分页；Task 1 的 API 测试固定该行为。
- 已单独归档的子节点在祖先归档后再恢复祖先时仍应保持归档；Task 2 的服务测试固定该行为。
- 搜索词命中无权资源时结果必须与资源不存在一致，不能通过计数、游标或错误推断；Task 1 的租户/授权测试固定该行为。
- 切换租户或快速切换项目时，迟到的查询和 mutation 不得污染当前列表、筛选条件或对话框；Task 3 和 Task 5 的组件测试固定该行为。
- demo 重启必须复用同一租户和固定资源，而 E2E 运行不得出现在 demo 首屏；Task 6 的 Python 集成测试固定该行为。

---

## File Map

### Resource backend

- `platform/services/business/src/main/resources/db/migration/V403__access_resource_workspace.sql`: 增加更新时间、归档字段和列表索引，并以 `created_at` 回填历史更新时间。
- `platform/services/business/src/main/java/cn/mjy/platform/access/ResourceListQuery.java`: 列表的 query/kind/archived/sort 值对象与参数解析。
- `platform/services/business/src/main/java/cn/mjy/platform/access/ResourceCursor.java`: `r2` 复合游标编码、解码和筛选上下文校验。
- `platform/services/business/src/main/java/cn/mjy/platform/access/ResourceView.java`: 对外返回 `updatedAt`、`archivedAt`。
- `platform/services/business/src/main/java/cn/mjy/platform/access/ResourceTreeRepository.java`: 授权后筛选、排序、分页和归档持久化。
- `platform/services/business/src/main/java/cn/mjy/platform/access/ResourceTreeService.java`: 查询规则、归档/恢复权限和审计事务。
- `platform/services/business/src/main/java/cn/mjy/platform/access/ResourceTreeController.java`: 查询参数及 archive/restore HTTP 契约。
- `platform/services/business/src/main/java/cn/mjy/platform/access/ResourceCapabilities.java`: 增加 `canArchive`、`canRestore`。
- `platform/services/business/src/main/java/cn/mjy/platform/survey/SurveyRepository.java`: 保存问卷标题时同步资源更新时间。

### Admin web

- `platform/apps/admin-web/src/app/App.tsx`: 全局产品框架、一级导航、中文身份显示与移动导航。
- `platform/apps/admin-web/src/app/router.tsx`: 工作区与 `SurveyShell` 嵌套路由。
- `platform/apps/admin-web/src/app/SurveyShell.tsx`: 面包屑、问卷状态和编辑/导入/预览/发布 tab。
- `platform/apps/admin-web/src/shared/api/resources.ts`: 新列表参数、资源字段和 mutation 客户端。
- `platform/apps/admin-web/src/features/workspace/WorkspacePage.tsx`: 工作区查询状态与页面编排。
- `platform/apps/admin-web/src/features/workspace/ProjectNav.tsx`: 仅承载项目和当前项目文件夹树。
- `platform/apps/admin-web/src/features/workspace/ResourceList.tsx`: 当前容器的可扫描资源列表与分页。
- `platform/apps/admin-web/src/features/workspace/WorkspaceToolbar.tsx`: 搜索、类型、状态、排序和新建菜单。
- `platform/apps/admin-web/src/features/workspace/ResourceActionDialogs.tsx`: 改名、移动、归档和恢复对话框。
- `platform/apps/admin-web/src/features/workspace/workspace.css`: 桌面/平板/移动工作区布局。

### Demo, E2E, and docs

- `platform/deploy/demo/admin-web-demo.compose.yml`: 持久化、仅回环端口的人工 demo 栈。
- `platform/deploy/demo/run-admin-web-demo.sh`: `start|stop|status|refresh-token` 生命周期和私密登录文件。
- `platform/deploy/demo/admin_web_demo.py`: 幂等租户、计划、引擎和固定资源种子。
- `platform/deploy/demo/.gitignore`: 忽略 `.runtime/` 私密文件。
- `platform/tests/e2e/test_admin_web_demo.py`: demo 幂等、租户隔离、SSL 策略和私密文件测试。
- `platform/apps/admin-web/e2e/authoring.spec.ts`: 工作区真实流程和 E2E 根项目归档。
- `platform/README.md`, `platform/docs/p2/progress.md`, `platform/docs/traceability/requirement-tests.md`: 同步真实完成度和测试证据。

## Execution Waves

- Wave 1 可并行：Task 1（资源查询后端）、Task 4（应用框架）、Task 6 的脚本骨架与单元测试。
- Wave 2：Task 2 依赖 Task 1；Task 3 依赖 Task 1/2 的 HTTP 契约。
- Wave 3：Task 5 依赖 Task 3/4；Task 6 完成真实栈种子时消费 Task 1/2 契约。
- Wave 4：Task 7 汇总所有接口与界面，执行真实浏览器门禁并同步文档。

### Task 1: Stable Resource Query Contract

**Files:**
- Create: `platform/services/business/src/main/resources/db/migration/V403__access_resource_workspace.sql`
- Create: `platform/services/business/src/main/java/cn/mjy/platform/access/ResourceListQuery.java`
- Modify: `platform/services/business/src/main/java/cn/mjy/platform/access/ResourceCursor.java`
- Modify: `platform/services/business/src/main/java/cn/mjy/platform/access/ResourceView.java`
- Modify: `platform/services/business/src/main/java/cn/mjy/platform/access/ResourceTreeRepository.java`
- Modify: `platform/services/business/src/main/java/cn/mjy/platform/access/ResourceTreeService.java`
- Modify: `platform/services/business/src/main/java/cn/mjy/platform/access/ResourceTreeController.java`
- Modify: `platform/services/business/src/main/java/cn/mjy/platform/survey/SurveyRepository.java`
- Test: `platform/services/business/src/test/java/cn/mjy/platform/access/ResourceTreeTest.java`
- Test: `platform/services/business/src/test/java/cn/mjy/platform/access/ResourceTreeApiTest.java`
- Test: `platform/services/business/src/test/java/cn/mjy/platform/access/AccessTenantIsolationTest.java`
- Test: `platform/services/business/src/test/java/cn/mjy/platform/survey/SurveyDraftTest.java`

**Interfaces:**
- Produces: `ResourceListQuery(String query, ResourceKind kind, ArchiveFilter archived, Sort sort)` with defaults `ACTIVE` and `UPDATED_DESC`; `ARCHIVED` lists directly archived nodes, while active visibility excludes every node with an archived ancestor.
- Produces: `ResourceTreeService.list(TenantContext, UUID, String, Integer, String, String, String, String)` for controller parameter forwarding; parsing lives in `ResourceListQuery`.
- Produces: `ResourceView(..., Instant createdAt, Instant updatedAt, Instant archivedAt)` and opaque `r2` cursors bound to all active filters.

- [ ] **Step 1: Write failing migration and service tests**

Add tests named `migrationBackfillsUpdatedAtFromCreatedAt`, `defaultListUsesBusinessOrderAcrossPageBoundaries`, `nameSortIsStableForNormalizedUnicodeNames`, `filtersAfterAuthorizationWithoutLeakingHiddenMatches`, and `rejectsCursorWhenSortOrFiltersChange`. Assert no duplicate or omitted IDs across equal timestamps and names.

- [ ] **Step 2: Run the focused backend tests and verify failure**

Run: `PLATFORM_DB_NAME=platform_admin_align platform/deploy/platform-dev/mvn.sh -Dtest=ResourceTreeTest,ResourceTreeApiTest,AccessTenantIsolationTest,SurveyDraftTest test`

Expected: FAIL because the migration fields, query parameters and `r2` cursor do not exist.

- [ ] **Step 3: Implement the migration and query value object**

Add `updated_at timestamptz`, backfill it from `created_at`, then make it `NOT NULL DEFAULT now()`; add nullable `archived_at` and active/archived parent-list indexes. Normalize names with Java NFC on writes and use deterministic PostgreSQL `COLLATE "C"` ordering.

- [ ] **Step 4: Implement authorization-scoped search, filtering, sort and cursor pagination**

Keep the recursive visible CTE as the candidate set, exclude resources with an archived ancestor for `active`, and apply query/kind/sort only inside that set. Encode sort mode, filters, complete position tuple and UUID in `r2`; reject malformed or mismatched cursors with `InvalidResourceRequestException`.

- [ ] **Step 5: Return and maintain timestamps**

Update create, rename, move and survey-title synchronization paths so resource business changes set `updated_at=now()`; reads must not change it. Return both new timestamps from list/get/create responses.

- [ ] **Step 6: Run focused and full access tests**

Run: `PLATFORM_DB_NAME=platform_admin_align platform/deploy/platform-dev/mvn.sh -Dtest='cn.mjy.platform.access.*Test' test`

Expected: PASS with stable cross-page ordering, bad cursor 400, and unchanged tenant isolation.

- [ ] **Step 7: Commit**

```bash
git add platform/services/business/src/main/resources/db/migration/V403__access_resource_workspace.sql platform/services/business/src/main/java/cn/mjy/platform/access platform/services/business/src/main/java/cn/mjy/platform/survey/SurveyRepository.java platform/services/business/src/test/java/cn/mjy/platform/access platform/services/business/src/test/java/cn/mjy/platform/survey/SurveyDraftTest.java
git commit -m "feat: add stable resource workspace queries"
```

### Task 2: Recoverable Resource Archive

**Files:**
- Modify: `platform/services/business/src/main/java/cn/mjy/platform/access/AccessAudit.java`
- Modify: `platform/services/business/src/main/java/cn/mjy/platform/access/ResourceCapabilities.java`
- Modify: `platform/services/business/src/main/java/cn/mjy/platform/access/ResourceTreeRepository.java`
- Modify: `platform/services/business/src/main/java/cn/mjy/platform/access/ResourceTreeService.java`
- Modify: `platform/services/business/src/main/java/cn/mjy/platform/access/ResourceTreeController.java`
- Modify: `platform/services/business/src/main/java/cn/mjy/platform/access/ResourceTreeErrorHandler.java`
- Test: `platform/services/business/src/test/java/cn/mjy/platform/access/ResourceTreeTest.java`
- Test: `platform/services/business/src/test/java/cn/mjy/platform/access/ResourceTreeApiTest.java`

**Interfaces:**
- Consumes: Task 1 `archived_at`, `ResourceListQuery`, `ResourceView`.
- Produces: `POST /v1/resources/{id}/archive`, `POST /v1/resources/{id}/restore`, and capability fields `canArchive`, `canRestore`.

- [ ] **Step 1: Write failing archive behavior tests**

Add tests named `archiveHidesTheWholeSubtreeFromActiveLists`, `restorePreservesAnIndependentlyArchivedDescendant`, `archiveRequiresEditAndKeepsCrossTenantIdsPrivate`, and `archiveAndRestoreAreAudited`. Archive state belongs only to the selected node; effective descendant hiding is computed from ancestors.

- [ ] **Step 2: Run tests and verify failure**

Run: `PLATFORM_DB_NAME=platform_admin_align platform/deploy/platform-dev/mvn.sh -Dtest=ResourceTreeTest,ResourceTreeApiTest test`

Expected: FAIL with missing archive/restore methods and capability fields.

- [ ] **Step 3: Implement transactional archive and restore**

Add repository updates guarded by current state, require `EDIT` on the selected resource, emit `access.resource.archive`/`access.resource.restore`, and make repeated same-state calls idempotent. Restoring a node must not clear `archived_at` on descendants.

- [ ] **Step 4: Expose HTTP endpoints and capabilities**

Return the updated `ResourceView`; preserve 404 privacy across tenants and unknown IDs. `canArchive` is true only for active editable resources; `canRestore` is true only for directly archived editable resources.

- [ ] **Step 5: Verify**

Run: `PLATFORM_DB_NAME=platform_admin_align platform/deploy/platform-dev/mvn.sh -Dtest='cn.mjy.platform.access.*Test' test`

Expected: PASS, including independent descendant archive preservation.

- [ ] **Step 6: Commit**

```bash
git add platform/services/business/src/main/java/cn/mjy/platform/access platform/services/business/src/test/java/cn/mjy/platform/access
git commit -m "feat: add recoverable resource archive"
```

### Task 3: Typed Admin Resource Client

**Files:**
- Modify: `platform/apps/admin-web/src/shared/api/resources.ts`
- Test: `platform/apps/admin-web/src/shared/api/resources.test.ts`
- Modify: `platform/apps/admin-web/src/test/server.ts`

**Interfaces:**
- Consumes: Task 1/2 HTTP resource contract.
- Produces: `ResourceFilters`, `resourceQueryKey(tenantId, parentId, filters)`, `renameResource`, `moveResource`, `archiveResource`, `restoreResource`.

- [ ] **Step 1: Write failing schema and URL tests**

Test `updatedAt`/nullable `archivedAt`, exact encoding of query/kind/archived/sort/cursor, filter-sensitive query keys, and rejection of malformed server payloads.

- [ ] **Step 2: Run and verify failure**

Run: `cd platform/apps/admin-web && npm test -- --run src/shared/api/resources.test.ts`

Expected: FAIL because the fields, filters and mutations are absent.

- [ ] **Step 3: Implement schemas, query keys and mutation functions**

Use `URLSearchParams` and Zod only; preserve opaque cursors exactly. Query keys must include every filter and tenant ID so stale tenant or sort data cannot merge.

- [ ] **Step 4: Verify unit, type and lint checks**

Run: `cd platform/apps/admin-web && npm test -- --run src/shared/api/resources.test.ts && npm run typecheck && npm run lint`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add platform/apps/admin-web/src/shared/api/resources.ts platform/apps/admin-web/src/shared/api/resources.test.ts platform/apps/admin-web/src/test/server.ts
git commit -m "feat: add typed resource workspace client"
```

### Task 4: Global and Survey Navigation Shells

**Files:**
- Create: `platform/apps/admin-web/src/app/App.test.tsx`
- Create: `platform/apps/admin-web/src/app/SurveyShell.tsx`
- Create: `platform/apps/admin-web/src/app/SurveyShell.test.tsx`
- Modify: `platform/apps/admin-web/src/app/App.tsx`
- Modify: `platform/apps/admin-web/src/app/router.tsx`
- Modify: `platform/apps/admin-web/src/app/styles.css`
- Modify: `platform/apps/admin-web/src/features/editor/EditorPage.tsx`
- Modify: `platform/apps/admin-web/src/features/import/ImportPage.tsx`
- Modify: `platform/apps/admin-web/src/features/preview/PreviewPage.tsx`
- Modify: `platform/apps/admin-web/src/features/publish/PublishPage.tsx`
- Modify: `platform/apps/admin-web/src/features/publish/VersionDetailPage.tsx`

**Interfaces:**
- Produces: protected nested route `/surveys/:surveyId/*` wrapped by `SurveyShell`.
- Produces: preserved query parameter `question` and return URL `/workspace?resource={surveyId}`.

- [ ] **Step 1: Write failing shell and route tests**

Assert global navigation contains 工作台/问卷 and omits unavailable 模板/待审批 until routes exist; `tenant_owner` is rendered as `租户管理员` with raw `actorId` only under “技术信息”. Assert survey tabs, breadcrumb loading, version detail selection, `question` preservation, mobile menu semantics and no `/admin` link.

- [ ] **Step 2: Run and verify failure**

Run: `cd platform/apps/admin-web && npm test -- --run src/app/App.test.tsx src/app/SurveyShell.test.tsx`

Expected: FAIL because `SurveyShell` and the navigation semantics are absent.

- [ ] **Step 3: Implement `AppShell` navigation and identity presentation**

Use Lucide icons, semantic nav landmarks and a 44px mobile menu button. Keep unavailable destinations hidden rather than disabled.

- [ ] **Step 4: Implement `SurveyShell` and nested routes**

Load survey/resource context with existing API clients, render breadcrumb/title/status/tab state, and change child pages to render workflow content without duplicate top-level navigation.

- [ ] **Step 5: Verify shell tests and existing page suites**

Run: `cd platform/apps/admin-web && npm test -- --run src/app src/features/editor/EditorPage.test.tsx src/features/import/ImportPage.test.tsx src/features/preview/PreviewPage.test.tsx src/features/publish/PublishPage.test.tsx`

Expected: PASS with route context preserved.

- [ ] **Step 6: Commit**

```bash
git add platform/apps/admin-web/src/app platform/apps/admin-web/src/features/editor platform/apps/admin-web/src/features/import platform/apps/admin-web/src/features/preview platform/apps/admin-web/src/features/publish
git commit -m "feat: add product navigation shells"
```

### Task 5: Workspace Information Architecture and Resource Actions

**Files:**
- Create: `platform/apps/admin-web/src/features/workspace/ProjectNav.tsx`
- Create: `platform/apps/admin-web/src/features/workspace/ResourceList.tsx`
- Create: `platform/apps/admin-web/src/features/workspace/WorkspaceToolbar.tsx`
- Create: `platform/apps/admin-web/src/features/workspace/ResourceActionDialogs.tsx`
- Modify: `platform/apps/admin-web/src/features/workspace/WorkspacePage.tsx`
- Modify: `platform/apps/admin-web/src/features/workspace/WorkspacePage.test.tsx`
- Modify: `platform/apps/admin-web/src/features/workspace/ResourceTree.tsx`
- Modify: `platform/apps/admin-web/src/features/workspace/workspace.css`

**Interfaces:**
- Consumes: Task 3 typed resource client and Task 4 `AppShell`.
- Produces: URL state `project`, `parent`, `resource`, `query`, `kind`, `archived`, `sort`; current-session recent resources stay in React memory only.

- [ ] **Step 1: Replace old expectations with failing IA tests**

Add component tests for project-only left navigation, current-container list, backend filter requests, opaque pagination, fixed Chinese labels, URL restoration, current-session recent resources, empty/error/retry states, and late tenant/project response isolation.

- [ ] **Step 2: Add failing resource-action tests**

Assert capabilities govern action visibility; rename and move refresh the right branches; archive removes a row from active view; restore removes it from archived view; archiving a survey warns that the public answer link is not closed; failed mutations retain form/query state and show a retryable alert.

- [ ] **Step 3: Run and verify failure**

Run: `cd platform/apps/admin-web && npm test -- --run src/features/workspace/WorkspacePage.test.tsx`

Expected: FAIL against the current all-purpose tree layout.

- [ ] **Step 4: Implement page state, project navigation and resource list**

Keep the left side limited to projects/current folder hierarchy and render current children as rows with kind, name and updated time. Track recently opened resources in component/session memory only and clear them on tenant change or reload. Send filters to the server; never client-sort fetched pages.

- [ ] **Step 5: Implement toolbar and capability-driven dialogs**

Use an icon “新建” menu, search input, type/status filters and sort control. Use focused dialogs for rename/move/archive/restore, restore focus after close, and invalidate only tenant-scoped affected query roots.

- [ ] **Step 6: Implement responsive CSS and accessibility states**

Desktop uses project nav plus main list; narrow screens use a project drawer and breadcrumb. Validate 44px controls, ellipsis plus accessible full names, focus visibility, loading announcements and no nested cards.

- [ ] **Step 7: Verify workspace and full frontend suites**

Run: `cd platform/apps/admin-web && npm test -- --run && npm run typecheck && npm run lint && npm run build`

Expected: all checks PASS and production bundle excludes the development token page.

- [ ] **Step 8: Commit**

```bash
git add platform/apps/admin-web/src/features/workspace platform/apps/admin-web/src/shared/api/resources.ts
git commit -m "feat: rebuild the resource workspace"
```

### Task 6: Isolated Human Demo Environment and Local SSL Policy

**Files:**
- Create: `platform/deploy/demo/.gitignore`
- Create: `platform/deploy/demo/admin-web-demo.compose.yml`
- Create: `platform/deploy/demo/run-admin-web-demo.sh`
- Create: `platform/deploy/demo/admin_web_demo.py`
- Create: `platform/tests/e2e/test_admin_web_demo.py`
- Modify: `platform/deploy/test/config.mysql.php`
- Modify: `platform/deploy/test/config.pgsql.php`
- Modify: `platform/deploy/test/run-admin-web-e2e.sh`
- Modify: `platform/tests/e2e/test_admin_web_gate.py`

**Interfaces:**
- Consumes: Task 1/2 resource endpoints for fixed seed lookup and recovery.
- Produces: `platform/deploy/demo/run-admin-web-demo.sh start|stop|status|refresh-token`.
- Produces: ignored `platform/deploy/demo/.runtime/access.json` mode `0600` containing URLs, non-secret usernames and local credentials; scripts print only this path, never values.
- Produces: a published demo survey containing the existing basic types, one already-supported advanced question theme, `zh-business` branding and a real respondent URL.

- [ ] **Step 1: Write failing demo lifecycle tests**

Test command parsing, deterministic compose project `adminweb-demo`, loopback-only ports, separate tenant code, idempotent seed reuse, required fixed resource names, `0600` credential file, redacted stdout/stderr, and refusal to claim production readiness before HTTPS/proxy checks pass.

- [ ] **Step 2: Run and verify failure**

Run: `python3 -m unittest platform/tests/e2e/test_admin_web_demo.py platform/tests/e2e/test_admin_web_gate.py`

Expected: FAIL because the demo lifecycle does not exist and test config lacks explicit SSL policy.

- [ ] **Step 3: Implement the demo Compose stack and lifecycle script**

Use persistent named volumes, random loopback host ports, generated secrets and a distinct Compose project. `start` builds/starts the stack, waits for the platform, gateway, admin-web and engine endpoints plus their databases, seeds idempotently and writes `access.json`; record the LimeSurvey address as `engineOperationsUrl`, expose it only on loopback, and never link it from the product UI. `stop` keeps volumes unless explicitly called with `--purge`, which must require interactive confirmation.

- [ ] **Step 4: Implement idempotent demo seed**

Persist non-secret tenant metadata separately, validate it through platform APIs on restart, and create exactly `客户体验研究 / 2026 Q4 / 品牌跟踪调查` when absent. Seed and publish a fixed definition that demonstrates basic questions, one supported advanced theme and `zh-business` branding, then record its respondent URL. Do not reuse `admin_web_gate.py` E2E tenant codes or timestamped names.

- [ ] **Step 5: Apply explicit SSL environment policy**

For local/demo/CI engine databases set `force_ssl=off` and `ssl_disable_alert=1`, then clear LimeSurvey settings cache. Add a production configuration assertion helper that requires HTTPS/proxy verification before `force_ssl=on`; do not alter upstream defaults globally.

- [ ] **Step 6: Verify unit tests and a two-start smoke cycle**

Run: `python3 -m unittest platform/tests/e2e/test_admin_web_demo.py platform/tests/e2e/test_admin_web_gate.py`

Run: `platform/deploy/demo/run-admin-web-demo.sh start && platform/deploy/demo/run-admin-web-demo.sh status && platform/deploy/demo/run-admin-web-demo.sh start`

Expected: both starts report the same demo tenant/resources, four services are healthy, SSL warning is absent locally, and only the private file path is printed.

- [ ] **Step 7: Commit**

```bash
git add platform/deploy/demo platform/deploy/test/config.mysql.php platform/deploy/test/config.pgsql.php platform/deploy/test/run-admin-web-e2e.sh platform/tests/e2e/test_admin_web_demo.py platform/tests/e2e/test_admin_web_gate.py
git commit -m "feat: add isolated admin demo environment"
```

### Task 7: Real Browser Gate, Documentation, and Delivery Evidence

**Files:**
- Modify: `platform/apps/admin-web/e2e/authoring.spec.ts`
- Modify: `platform/apps/admin-web/playwright.config.ts`
- Modify: `platform/tests/e2e/admin_web_gate.py`
- Modify: `platform/tests/e2e/test_admin_web_gate.py`
- Modify: `platform/README.md`
- Modify: `platform/docs/p2/progress.md`
- Modify: `platform/docs/traceability/requirement-tests.md`
- Modify: `docs/superpowers/specs/2026-10-09-admin-product-alignment-design.md`

**Interfaces:**
- Consumes: Tasks 1–6 complete product flow.
- Produces: sanitized browser evidence for 768px, 1024px, 1440px and mobile; E2E root project archived after successful suite completion.

- [ ] **Step 1: Add failing real-browser scenarios**

Assert stable demo names, no `E2E` timestamp resources in demo, search/filter/sort request behavior, edit/import/preview/publish round trip back to the same resource, archive/restore, 44px mobile controls, and no horizontal overflow at all required widths.

- [ ] **Step 2: Add E2E cleanup through archive**

Record the run root project ID and archive it only after successful E2E assertions. On failure preserve it and write only the resource ID into sanitized evidence; never add a delete endpoint.

- [ ] **Step 3: Run frontend and backend regression suites**

Run: `cd platform/apps/admin-web && npm test -- --run && npm run typecheck && npm run lint && npm run build`

Run: `PLATFORM_DB_NAME=platform_admin_align platform/deploy/platform-dev/mvn.sh test`

Expected: PASS.

- [ ] **Step 4: Run the real-stack browser gate**

Run: `SURVEY_TEST_PREFIX=admin-align platform/deploy/test/run-admin-web-e2e.sh --fresh`

Expected: desktop and mobile Playwright projects PASS; platform, gateway and engine verification PASS; private credentials are removed.

- [ ] **Step 5: Capture and inspect product evidence**

Use Playwright screenshots from the running demo stack at 768px, 1024px, 1440px and Pixel 7; inspect each image for overlap, truncation, test-data pollution and product/engine entry confusion. Complete the workspace and survey tabs once using keyboard-only navigation, including visible focus and action dialogs. Store only sanitized images under the existing audit directory.

- [ ] **Step 6: Synchronize repository documentation**

Update existing README/progress/traceability documents with the date, exact test commands/results, product-visible status, remaining Phase C/D work and PR #10 dependency resolution. Change the design status from “待用户书面复核” to “已确认，Phase A+B 已交付” only after all gates pass.

- [ ] **Step 7: Commit**

```bash
git add platform/apps/admin-web/e2e platform/apps/admin-web/playwright.config.ts platform/tests/e2e platform/README.md platform/docs docs/audits docs/superpowers/specs/2026-10-09-admin-product-alignment-design.md
git commit -m "test: verify aligned admin product flow"
```

- [ ] **Step 8: Controller-only delivery closure**

Run a final whole-branch review, then follow `superpowers:finishing-a-development-branch`. After the branch is pushed, update the registered Obsidian project overview with branch, commit, test evidence, delivered scope, Phase C/D remainder and GitHub traceability; never include credentials.

## Deferred Plans

- Phase C will receive its own plan for editor responsive breakpoints,中文业务文案、保存/版本状态和专项无障碍。
- Phase D1 will receive its own plan for isolated LimeSurvey runtime preview jobs and TTL cleanup.
- Phase D2 will receive separate vertical-slice plans for template library, branding, and the first three advanced question types.
