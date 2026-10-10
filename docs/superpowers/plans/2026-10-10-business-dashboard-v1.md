# Business Dashboard v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将登录默认入口升级为租户隔离、角色感知、可恢复最近工作的真实业务工作台，让既有预览、发布、答卷和导出能力可以从首页被发现并直接进入。

**Architecture:** 在 Java 平台新增薄的 `dashboard` 应用层读模型，用一个 `REPEATABLE_READ` 只读事务批量聚合现有资源、审批、发布、预览、答卷和导出事实；`V933` 只持久化用户级最近工作并通过现有租户上下文与资源授权过滤。React 管理端用独立 Zod 契约与 TanStack Query 页面消费单一聚合接口，保留 `/workspace` 作为资源管理，并由共享路由集成者统一修改 `App.tsx`、`router.tsx` 和 `SurveyShell.tsx`。

**Tech Stack:** Java 21、Spring Boot 4.1.1、JDBC/PostgreSQL/Flyway、React 19、TypeScript 5.9、TanStack Query 5、Zod、Vitest、Testing Library、Playwright 1.64、现有 Agent Harness。

**Spec:** `docs/superpowers/specs/2026-10-10-business-dashboard-v1-design.md`

**Parent plan:** `docs/superpowers/plans/2026-10-09-platform-productization-production-upgrade.md`

**Audit baseline:** `docs/audits/2026-10-09-product-alignment/14-full-development-report.md`

**Status:** 已于 2026-10-10 获用户书面确认

## Global Constraints

- 本计划是发布后闭环与模板/品牌之间的 D1 产品可发现性切片，不重编号既有七个纵向波次，也不宣称其他波次完成。
- 父计划“冻结新后端业务包”继续有效；`cn.mjy.platform.dashboard` 仅为既有事实的应用层只读聚合，不新增业务状态机、计费模型或领域真源。
- 工作台不得在浏览器逐问卷 N+1 请求；摘要、任务和问卷列表必须来自同一服务端逻辑快照。
- 所有数量先按当前租户与调用者权限过滤；无权区块通过 `visibleSections` 消失，不能用零或错误差异泄露对象存在性。
- 最近工作只接受 `surveyId`、受控 `page` 枚举和受约束 `version`；恢复路径由服务端生成，客户端不能提交任意 URL。
- 每个用户/租户最多保留 50 个不同恢复目标；失权、归档、跨租户记录在读时过滤。
- 业务文案使用中文；不显示裸枚举、测试管理员硬编码身份、占位导航或未实现能力。
- 不新增第三方依赖，不修改 LimeSurvey 上游源码，不改变单问卷编辑、预览、发布、答卷和导出状态机。
- 用户已于 2026-10-10 明确要求在计划后进入多 Agent 开发；该授权覆盖本计划限定的 `V933` migration 和为复用既有授权所必需的 `access` 只读接口变更，不覆盖其他认证、租户、生产、Release 或密钥路径。
- 每个 Task 遵循 RED -> GREEN -> scoped review -> commit；共享路由文件只由 Task 6 集成者修改。

## Review Focus

- 跨租户或失权资源：统一不可见，不得通过计数、最近记录、名称、状态或不同错误码推断。
- 同一快照一致性：并发发布、预览关闭或导出状态变化时，摘要与列表不能来自不同事务时点。
- 最近工作输入：非法页面、`version` 组合、重复目标、超过 50 条和归档问卷必须有确定行为。
- 部分依赖故障：整个聚合返回统一 `503`，前端保留旧数据并标注可能过期，不能显示伪造全零。
- 响应式和恢复导航：`819/820/1179/1180px` 无主操作裁切或页面横向滚动，所有服务端路径只落到现有路由。

---

## Delivery Order And Agent Ownership

| 顺序 | Task | Agent ownership | 依赖 |
|---|---|---|---|
| 1 | 契约与事实冻结 | Controller / docs | 无 |
| 2 | 最近工作持久化 | Backend persistence agent | Task 1 |
| 3 | 聚合读模型与 API | Backend aggregate agent | Task 2 |
| 4 | 前端契约与查询 | Frontend data agent | Task 1，可与 Task 2 并行 |
| 5 | Dashboard 页面 | Frontend page agent | Task 4 |
| 6 | 路由、导航与最近访问登记 | Integration agent | Tasks 3-5 |
| 7 | 真实 E2E、能力与交付同步 | Verification/docs agent | Task 6 |

Task 2 与 Task 4 写集互不重叠，可并行；其余按依赖顺序合并。Task 6 是 `App.tsx`、`router.tsx`、`SurveyShell.tsx` 的唯一集成者，其他 Agent 不得改这些共享文件。

### Task 1: Freeze Dashboard Contract And Current-Fact Baseline

**Files:**
- Modify: `docs/superpowers/specs/2026-10-10-business-dashboard-v1-design.md`
- Create: `docs/superpowers/plans/2026-10-10-business-dashboard-v1.md`
- Create: `.superpowers/sdd/2026-10-10-business-dashboard-v1/progress.md`

**Interfaces:**
- Consumes: confirmed design, parent productization plan, historical audit and current repository state.
- Produces: immutable API field names, route names, migration number `V933`, task ownership and gate contract used by Tasks 2-7.

- [x] **Step 1: Recheck current facts and freeze the delta**

Confirm latest migration is `V932`, `/` still redirects to `/workspace`, no `/dashboard` feature exists, account name is hardcoded, and existing preview/publish/responses/export pages are real current capabilities.

- [x] **Step 2: Record design confirmation and historical-audit correction rule**

Mark the design confirmed. Treat the 2026-10-09 report as a baseline only; later code evidence supersedes statements about missing real preview, delivery links, responses and exports.

- [x] **Step 3: Run documentation checks**

Run: `scripts/agent-harness --python tools/agent-harness/drills/run_drills.py --check-docs`

Run: `git diff --check`

Expected: both PASS after the Harness active-plan transition is recorded for this Milestone.

- [x] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-10-10-business-dashboard-v1-design.md docs/superpowers/plans/2026-10-10-business-dashboard-v1.md
git commit -m "docs: plan business dashboard v1"
```

### Task 2: Persist Tenant-Scoped Recent Work

**Files:**
- Create: `platform/services/business/src/main/resources/db/migration/V933__dashboard_recent_work.sql`
- Create: `platform/services/business/src/main/java/cn/mjy/platform/dashboard/DashboardPage.java`
- Create: `platform/services/business/src/main/java/cn/mjy/platform/dashboard/RecentWorkCommand.java`
- Create: `platform/services/business/src/main/java/cn/mjy/platform/dashboard/RecentWorkView.java`
- Create: `platform/services/business/src/main/java/cn/mjy/platform/dashboard/DashboardRecentWorkRepository.java`
- Create: `platform/services/business/src/test/java/cn/mjy/platform/dashboard/DashboardRecentWorkRepositoryTest.java`
- Create: `platform/services/business/src/test/java/cn/mjy/platform/dashboard/DashboardRecentWorkTenantIsolationTest.java`

**Interfaces:**
- Consumes: trusted tenant/actor context and existing survey resource visibility.
- Produces: `DashboardPage { EDIT, IMPORT, PREVIEW, PUBLISH, RESPONSES, VERSION }`; `upsert(tenantId, actorId, command, now)`; `findVisible(tenantId, actorId, limit)`; `RecentWorkView` with server-generated `targetPath`.

- [x] **Step 1: Write failing repository and database-guard tests**

Cover valid page/version pairs, invalid pairs, same-target timestamp update, cross-tenant isolation, actor isolation, invisible/archived survey filtering, deterministic ordering and 51st-target eviction to 50.

- [x] **Step 2: Run focused tests and confirm RED**

Run: `PLATFORM_DB_NAME=platform_dashboard_t2_red platform/deploy/platform-dev/mvn.sh clean test -Dtest='cn.mjy.platform.dashboard.DashboardRecentWork*'`

Expected: FAIL because `V933` and dashboard repository types do not exist.

- [x] **Step 3: Add `V933__dashboard_recent_work.sql`**

Create a tenant/actor/survey/page/version keyed table with UTC visit time, referential constraints consistent with existing IDs, an index supporting `visited_at DESC`, and the repository's bounded-retention delete. Do not accept a stored client URL.

- [x] **Step 4: Implement recent-work types and repository**

Validate page/version combinations before SQL; use server-side target-path mapping and existing visibility predicates. Upsert and retention execute atomically.

- [x] **Step 5: Run focused and migration tests**

Run: `PLATFORM_DB_NAME=platform_dashboard_t2_green platform/deploy/platform-dev/mvn.sh clean test -Dtest='cn.mjy.platform.dashboard.DashboardRecentWork*'`

Expected: PASS with tenant isolation and 50-item retention evidence.

- [x] **Step 6: Commit**

```bash
git add platform/services/business/src/main/resources/db/migration/V933__dashboard_recent_work.sql platform/services/business/src/main/java/cn/mjy/platform/dashboard platform/services/business/src/test/java/cn/mjy/platform/dashboard
git commit -m "feat: persist dashboard recent work"
```

### Task 3: Build The Snapshot Dashboard Aggregate API

**Files:**
- Create: `platform/services/business/src/main/java/cn/mjy/platform/dashboard/DashboardSummary.java`
- Create: `platform/services/business/src/main/java/cn/mjy/platform/dashboard/DashboardTaskView.java`
- Create: `platform/services/business/src/main/java/cn/mjy/platform/dashboard/DashboardSurveyView.java`
- Create: `platform/services/business/src/main/java/cn/mjy/platform/dashboard/DashboardView.java`
- Create: `platform/services/business/src/main/java/cn/mjy/platform/dashboard/DashboardQuery.java`
- Create: `platform/services/business/src/main/java/cn/mjy/platform/dashboard/DashboardRepository.java`
- Create: `platform/services/business/src/main/java/cn/mjy/platform/dashboard/DashboardService.java`
- Create: `platform/services/business/src/main/java/cn/mjy/platform/dashboard/DashboardController.java`
- Create: `platform/services/business/src/main/java/cn/mjy/platform/dashboard/DashboardErrorHandler.java`
- Modify only if required: `platform/services/business/src/main/java/cn/mjy/platform/access/ResourceCapabilities.java`
- Create: `platform/services/business/src/test/java/cn/mjy/platform/dashboard/DashboardApiTest.java`
- Create: `platform/services/business/src/test/java/cn/mjy/platform/dashboard/DashboardTenantIsolationTest.java`
- Create: `platform/services/business/src/test/java/cn/mjy/platform/dashboard/DashboardSnapshotConsistencyTest.java`

**Interfaces:**
- Consumes: Task 2 recent-work repository plus existing resource, approval, publish attempt, preview, response-summary and export records.
- Produces: `GET /v1/dashboard?surveyLimit=20&taskLimit=20`; `POST /v1/dashboard/recent-work` with `204`; immutable response names and enums from the spec.

- [x] **Step 1: Write failing API, authorization and query-count tests**

Assert `1..50` limits, default 20, `422` invalid input, uniform `404` invisible recent target, `503` dependency failure, role-specific `visibleSections`, stable task/survey ordering, no cross-tenant leakage and a bounded fixed query count independent of survey count.

- [x] **Step 2: Run focused tests and confirm RED**

Run: `PLATFORM_DB_NAME=platform_dashboard_t3_red platform/deploy/platform-dev/mvn.sh clean test -Dtest='cn.mjy.platform.dashboard.Dashboard*'`

Expected: FAIL because aggregate API types do not exist.

- [x] **Step 3: Implement batch projections in `DashboardRepository`**

Use set-based SQL for surveys, tasks, summary inputs and recent work. Do not call existing HTTP controllers or loop over survey IDs. Count only authorized rows.

- [x] **Step 4: Implement one read-only repeatable-read service transaction**

`DashboardService.getDashboard(int surveyLimit, int taskLimit)` returns one `generatedAt` snapshot. Dependency exceptions map to a stable `dashboard_temporarily_unavailable` response and HTTP `503`.

- [x] **Step 5: Implement controller and recent-work endpoint**

Read tenant and actor only from trusted request context. Validate request shapes, call the Task 2 repository, and return no secrets, download URLs or internal errors.

- [x] **Step 6: Run focused and complete platform gates**

Run: `PLATFORM_DB_NAME=platform_dashboard_t3_green platform/deploy/platform-dev/mvn.sh clean test -Dtest='cn.mjy.platform.dashboard.Dashboard*'`

Run: `platform/deploy/test/run-platform-tests.sh`

Expected: focused dashboard tests and the complete platform reconciliation pass with zero failures.

- [x] **Step 7: Commit**

```bash
git add platform/services/business/src/main/java/cn/mjy/platform/dashboard platform/services/business/src/test/java/cn/mjy/platform/dashboard
git commit -m "feat: expose dashboard aggregate"
```

### Task 4: Add The Typed Frontend Dashboard Client

**Files:**
- Create: `platform/apps/admin-web/src/shared/api/dashboard.ts`
- Create: `platform/apps/admin-web/src/shared/api/dashboard.test.ts`
- Modify: `platform/apps/admin-web/src/shared/api/schemas.ts`

**Interfaces:**
- Consumes: Task 3 JSON contract.
- Produces: `getDashboard(api, options?, signal?)`, `recordRecentWork(api, command, signal?)`, `dashboardQueryKey(tenantId)`, and exported `DashboardView`, `DashboardTask`, `DashboardSurvey`, `RecentWork` types.

- [x] **Step 1: Write failing schema and client tests**

Cover every enum, nullable response count, visible sections, limit serialization, `204` mutation, unknown/missing field rejection, invalid target path rejection and tenant-specific query keys.

- [x] **Step 2: Run focused tests and confirm RED**

Run: `npm test -- --run src/shared/api/dashboard.test.ts`

Expected: FAIL because dashboard client and schemas do not exist.

- [x] **Step 3: Implement Zod schemas and API functions**

Keep raw enums internal to typed rendering; allow only server-generated relative paths matching the existing survey route set.

- [x] **Step 4: Run focused frontend tests**

Run: `npm test -- --run src/shared/api/dashboard.test.ts`

Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add platform/apps/admin-web/src/shared/api/dashboard.ts platform/apps/admin-web/src/shared/api/dashboard.test.ts platform/apps/admin-web/src/shared/api/schemas.ts
git commit -m "feat: add dashboard api client"
```

### Task 5: Build The Responsive Dashboard Page

**Files:**
- Create: `platform/apps/admin-web/src/features/dashboard/DashboardPage.tsx`
- Create: `platform/apps/admin-web/src/features/dashboard/DashboardPage.test.tsx`
- Create: `platform/apps/admin-web/src/features/dashboard/dashboard.css`

**Interfaces:**
- Consumes: Task 4 client/types and current auth session tenant ID.
- Produces: `DashboardPage` with summary, task list, survey list, recent work, loading/empty/403/503/stale states and refresh action.

- [x] **Step 1: Write failing component tests**

Cover loading skeleton, all-empty success, hidden unauthorized sections, every task/action route, Chinese status labels, 403, initial 503, refresh failure retaining stale data, no hardcoded identity, accessible icon refresh and empty recent work.

- [x] **Step 2: Run focused tests and confirm RED**

Run: `npm test -- --run src/features/dashboard/DashboardPage.test.tsx`

Expected: FAIL because the feature does not exist.

- [x] **Step 3: Implement the page and stable layout**

Use unframed full-width sections, fixed summary dimensions, Lucide refresh icon, semantic headings/table/list, `aria-live` for async status, and no placeholder links.

- [x] **Step 4: Implement responsive CSS at exact boundaries**

`>=1180px` uses four summary columns and full tables; `820..1179px` uses two columns and compact lists; `<820px` keeps primary actions adjacent to titles without page-level horizontal scrolling.

- [x] **Step 5: Run focused tests, lint and typecheck**

Run: `npm test -- --run src/features/dashboard/DashboardPage.test.tsx`

Run: `npm run lint`

Run: `npm run typecheck`

Expected: PASS.

- [x] **Step 6: Commit**

```bash
git add platform/apps/admin-web/src/features/dashboard
git commit -m "feat: build business dashboard"
```

### Task 6: Integrate Routes, Navigation And Recent-Work Registration

**Files:**
- Modify: `platform/apps/admin-web/src/app/App.tsx`
- Modify: `platform/apps/admin-web/src/app/App.test.tsx`
- Modify: `platform/apps/admin-web/src/app/router.tsx`
- Create: `platform/apps/admin-web/src/app/router.test.tsx`
- Modify: `platform/apps/admin-web/src/app/SurveyShell.tsx`
- Modify: `platform/apps/admin-web/src/app/SurveyShell.test.tsx`
- Modify: `platform/apps/admin-web/src/features/workspace/WorkspacePage.tsx`
- Modify: `platform/apps/admin-web/src/features/workspace/WorkspacePage.test.tsx`

**Interfaces:**
- Consumes: Tasks 3-5.
- Produces: `/ -> /dashboard`, `/dashboard`, `/workspace`, `/surveys/:id -> edit`, two-item global navigation, real session identity, and post-load recent-work registration for `edit|import|preview|publish|responses|version`.

- [ ] **Step 1: Write failing route and navigation tests**

Assert all redirects, active destinations, brand destination, “项目与问卷” label, session-derived account display, retained workspace query compatibility and no duplicate workbench/survey destination.

- [ ] **Step 2: Write failing recent-work registration tests**

For each survey route, assert one controlled mutation only after the survey/resource is valid; failed page loads and arbitrary URL/query values must not register. Registration failure must not block the business page.

- [ ] **Step 3: Run focused tests and confirm RED**

Run: `npm test -- --run src/app/App.test.tsx src/app/router.test.tsx src/app/SurveyShell.test.tsx src/features/workspace/WorkspacePage.test.tsx`

Expected: FAIL on old redirect/navigation/hardcoded account and absent registration.

- [ ] **Step 4: Integrate Dashboard route and shared navigation**

Lazy-load `DashboardPage`; make `/dashboard` the default; add survey index redirect; use authenticated `session.me.actorId` with the translated role label in the account summary instead of inventing a test identity.

- [ ] **Step 5: Register recent work from stable survey routes**

Centralize page-enum derivation in `SurveyShell`; version routes submit a positive parsed version, all others submit `null`. Invalidate only the current tenant's dashboard query after successful registration.

- [ ] **Step 6: Remove component-memory recent surveys from Workspace**

Remove `recentResources` and its tests/UI so there is one durable recent-work truth. Preserve resource browsing, filters and context restoration.

- [ ] **Step 7: Run complete Admin Web gates**

Run: `npm run lint`

Run: `npm run typecheck`

Run: `npm test -- --run`

Run: `npm run build`

Run: `npm run assert:production-bundle`

Expected: all PASS and no production bundle contains `/dev/token`.

- [ ] **Step 8: Commit**

```bash
git add platform/apps/admin-web/src/app platform/apps/admin-web/src/features/workspace
git commit -m "feat: make dashboard the product home"
```

### Task 7: Prove The Vertical Journey And Synchronize Product Evidence

**Files:**
- Modify: `platform/apps/admin-web/e2e/admin-workspace.spec.ts`
- Modify or create: `platform/apps/admin-web/e2e/dashboard.spec.ts`
- Modify: `platform/tools/productization/capabilities.json`
- Generate: `platform/docs/productization/capability-map.md`
- Modify: `platform/docs/p2/progress.md`
- Modify: `platform/README.md`
- Create: `docs/audits/2026-10-09-product-alignment/15-business-dashboard-v1-follow-up.md`
- Modify: `docs/superpowers/plans/2026-10-10-business-dashboard-v1.md`
- Modify: `.superpowers/sdd/2026-10-10-business-dashboard-v1/progress.md`

**Interfaces:**
- Consumes: complete Dashboard vertical slice.
- Produces: real-browser acceptance, capability evidence, historical-audit addendum and synchronized repository/GitHub/Obsidian status.

- [ ] **Step 1: Write failing real-browser journeys**

Cover login landing on dashboard, publish-page navigation, responses/export navigation, approver-only task visibility, recent-work round trip, and mobile navigation. Add DOM assertions at 819, 820, 1179 and 1180 px for no page overflow, overlap or clipped primary actions.

- [ ] **Step 2: Run the focused browser spec and confirm RED**

Run: `platform/deploy/test/run-admin-web-e2e.sh --fresh`

Expected: FAIL before the new browser journey is wired into the real stack.

- [ ] **Step 3: Fix only integration defects exposed by the real stack**

Do not weaken selectors, skip role checks or replace real responses with static fixtures.

- [ ] **Step 4: Run vertical and regression gates**

Run: `platform/deploy/test/run-platform-tests.sh`

Run: `platform/deploy/test/run-admin-web-e2e.sh --fresh`

Run: `platform/deploy/test/run-p1-e2e.sh`

Run: `scripts/agent-harness --python -m unittest discover -s platform/tools/productization -p 'test_render_capabilities.py'`

Run: `scripts/agent-harness --python platform/tools/productization/render_capabilities.py --check`

Run: `git diff --check`

Expected: all applicable gates PASS with no hidden skip; screenshots at desktop/tablet/mobile are manually inspected for overlap and clipping.

- [ ] **Step 5: Update capability and audit evidence**

Only promote dashboard-specific evidence actually proven by Task 7. The addendum must list which 2026-10-09 audit statements are superseded and which missing waves remain unchanged.

- [ ] **Step 6: Perform independent security, product UX and whole-branch reviews**

Bind reports to the final implementation commit and resolve findings within the Harness repair budget. Security review must emphasize tenant/count leakage and server-generated paths; UX review must inspect real screenshots at all four breakpoints.

- [ ] **Step 7: Commit, push and synchronize Obsidian**

```bash
git add platform/apps/admin-web/e2e platform/tools/productization platform/docs platform/README.md docs/audits/2026-10-09-product-alignment/15-business-dashboard-v1-follow-up.md docs/superpowers/plans/2026-10-10-business-dashboard-v1.md
git commit -m "test: verify business dashboard journey"
git push origin feat/admin-product-alignment
```

Update the existing Survey blueprint/progress note with date, branch, final commit, test evidence, completed Dashboard boundary, remaining Wave 2-6 work and the next approved Milestone. Do not record credentials or raw logs.

## Final Acceptance

- [ ] `/` and successful login land on `/dashboard`; `/workspace` remains the complete resource manager.
- [ ] Dashboard summary, tasks, surveys and recent work come from one authorized server snapshot without per-survey HTTP fan-out.
- [ ] Recent work persists across page changes and reloads, is bounded to 50 targets, and filters cross-tenant, archived and lost-access resources.
- [ ] Author, approver and data roles see only authorized sections, counts, objects and actions.
- [ ] All task and survey actions target existing working pages; no placeholder or disabled future navigation is present.
- [ ] Initial load, empty, partial visibility, 403, 503, refresh failure and stale-data states are covered in Chinese.
- [ ] Desktop, tablet and mobile real-browser journeys pass at 819/820/1179/1180 boundaries without overlap, clipping or page overflow.
- [ ] Platform, Admin Web, real E2E, capability, traceability and diff gates pass with no unapproved skip.
- [ ] Independent reviews approve the final commit and repository/GitHub/Obsidian evidence agrees on scope and remaining work.

## Explicitly Deferred

- Wave 2: template browsing/copying and brand configuration.
- Wave 4: contacts, departments, audiences, batch delivery, reminders and unsubscribe.
- Wave 5: Phase C editor layout and advanced question types.
- Wave 6: tenant plans, engine instances, organization connections and platform template operations.
- Native four-runner Release acceptance, production deployment and public TLS remain governed by the parent plan and are not silently completed by this Dashboard slice.
