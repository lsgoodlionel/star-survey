# Wave 2 Template And Branding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把现有模板、资产、品牌和多语言后端能力接入 Admin Web，形成从模板创建问卷、配置品牌、真实预览、发布并完成品牌化作答的可上线闭环。

**Architecture:** 保留 `SurveyTemplateController`、`AssetController` 和问卷草稿作为业务事实来源，Admin Web 使用类型化 client 和 TanStack Query 接入。品牌文本继续保存到草稿顶层 `branding`/`translations`，品牌图片新增受控作者态资产引用，并在预览/发布边界固定资产版本、转换为 `zh-business` 可编译的本地主题文件；不开放 CSS、JavaScript、HTML 或外部 URL。

**Tech Stack:** Java 21、Spring Boot 4.1、PostgreSQL 16、Python 3.12、React 19、TypeScript 5.9、TanStack Query 5、React Hook Form 7、Zod 4、Vitest 5、Playwright 1.64、Docker Compose v2、LimeSurvey。

**Spec:** `docs/superpowers/specs/2026-10-11-template-branding-wave2-design.md`

## Global Constraints

- 执行前先关闭 Dashboard Task 7 的 GitHub 治理门和外部同步，不得覆盖未完成的 `activePlan`。
- 子 Agent 模型由当前运行时能力清单和任务复杂度决定；每次运行记录实际模型与 reasoning effort，不得静默替换。
- 本 Wave 不新增无关后端业务包，不开放租户 CSS、JavaScript、HTML 或外部资源 URL。
- 租户、操作者和权限只从受信令牌上下文读取；前端隐藏操作不代替后端授权。
- 草稿保存必须携带 `expectedVersion`；冲突不自动覆盖、不绕过乐观锁。
- 模板必须在入库前剥离参与者、访问策略、管理员联系人和平台生成字段；复制后与来源模板相互独立。
- 预览与发布都必须固定品牌资产版本；资产后续更新不得改变已经发布的版本。
- LimeSurvey 的 RemoteControl `import_survey` 不能从 LSS/LSA 携带主题图片。品牌资产必须在导入取得 `sid` 后，经既有 `MjyQuestionExtensions` 签名通道落到 `upload/surveys/<sid>/images/`，并由受限端点写入每问卷主题选项；不得把作者 URL、任意路径或未校验字节交给引擎。
- 一项能力只有在需求、后端、前端、真实流程、生产和追溯六层证据齐全时才能标记完成。

## Review Focus

- 品牌资产上传后刷新、预览、发布和历史版本回放必须一致，不能出现管理端缩略图正常而作答页图片丢失。
- 模板的 draft、pending、rejected、published、unpublished 状态必须只显示合法动作，并正确处理并发 409。
- 编辑器和品牌页同时基于旧草稿版本保存时，后提交者必须看到中文冲突提示，不能静默覆盖另一页的修改。
- 平台模板与租户模板的权限、详情和复制接口必须保持隔离，普通租户不能触发 `/v1/platform/**`。
- 附加语言只允许引用真实题组、题目、子题和选项；缺失译文必须回退基础语言而不是渲染空白。

## 2026-10-11 Validation Note

- 已增加本地开发 Compose 的可选 `ai` profile；默认 dev、`test` 和生产 Compose 不依赖 Ollama。
- 已增加 `platform/deploy/test/ai-health-check.sh` 及无端口 stub 测试：健康检查、远程端点跳过/失败降级和可选生成 smoke 均已验证。
- 本机 Ollama API 可达但没有已安装模型，生成 smoke 当前跳过；远程 `openclaw.lsgood.cn` 根 HTTPS 可达，但公开 `/health` 不通过，未配置为产品硬依赖。
- 已增加 `platform/tools/publish-gateway/tools/local_brand_asset_bundle_smoke.py` 和对应测试，覆盖临时 PNG/JPEG 的 base64、大小、SHA-256、固定文件及 LSS 相对路径一致性。
- 上述图片 smoke 在 2026-10-11 仅是 local-test-only；2026-10-27 已补全 Task 2 的真实字节边界和双库 E2E，当前证据见 `docs/agent/run-history/2026-10-12-wave2-model-handoff.md` §14。Task 3–8 与 Wave 2 整体闭环仍待后续实施。

---

### Task 1: Close Dashboard Governance And Activate The Wave 2 Plan

**Files:**
- Modify: `.github/workflows/agent-governance.yml`
- Modify: `tools/agent-harness/tests/test_governance.py`
- Modify: `tools/agent-harness/drills/run_drills.py`
- Modify after hosted success: `docs/agent/HARNESS_DELIVERY.json`
- Modify after hosted success: `docs/agent/PROGRESS.md`
- Modify after hosted success: `docs/agent/HARNESS_HOST.json`
- Modify after hosted success: `docs/agent/PROJECT_MEMORY.md`

**Interfaces:**
- Consumes: Dashboard implementation commit and review evidence already bound in `HARNESS_DELIVERY.json`.
- Produces: PR jobs checkout the source branch HEAD instead of GitHub's temporary merge commit; Dashboard Task 7 is closed; `activePlan` becomes this plan only after repository and knowledge-base sync succeed.

- [x] **Step 1: Write failing governance tests**

Add tests named `test_pull_request_checks_out_head_sha` and `test_docs_gate_compares_delivery_to_checked_out_head`; assert that PR validation uses `github.event.pull_request.head.sha`, push validation uses `github.sha`, and no temporary merge SHA is accepted as delivery evidence.

- [x] **Step 2: Run the focused test and confirm RED**

Run: `scripts/agent-harness --python -m unittest discover -s tools/agent-harness/tests -p 'test_governance.py' -v`

Expected: FAIL because the workflow currently relies on the default PR merge checkout.

- [x] **Step 3: Implement event-aware checkout and fail-closed docs validation**

Set the checkout ref explicitly for `pull_request` while preserving `fetch-depth: 0`; make the docs drill compare the checked-out commit with `implementationCommit` and `evidence.headCommit`, without accepting arbitrary environment overrides.

- [x] **Step 4: Run local Harness gates**

Run: `scripts/agent-harness --python tools/agent-harness/run_tests.py --exclude-substring linux_wrapper --fail-on-skip`

Run: `scripts/agent-harness --python tools/agent-harness/drills/run_drills.py --check-docs`

Expected: all tests pass with zero skips and the docs check binds to branch HEAD.

- [x] **Step 5: Commit, push, and verify the hosted workflow**

```bash
git add .github/workflows/agent-governance.yml tools/agent-harness/tests/test_governance.py tools/agent-harness/drills/run_drills.py
git commit -m "fix: bind governance checks to pull request head"
git push
```

Expected: the GitHub Actions `agent-governance` run succeeds on the PR head SHA.

- [x] **Step 6: Close Dashboard delivery and switch the active plan**

Only after hosted success, update Dashboard Task 7/final acceptance, GitHub sync and Obsidian evidence; then set `HARNESS_HOST.json.activePlan` and `HARNESS_DELIVERY.json.planPath` to this plan and initialize Wave 2 with pending evidence fields.

- [x] **Step 7: Commit the control-plane transition**

```bash
git add docs/agent/HARNESS_DELIVERY.json docs/agent/PROGRESS.md docs/agent/HARNESS_HOST.json docs/agent/PROJECT_MEMORY.md docs/superpowers/specs/2026-10-11-template-branding-wave2-design.md docs/superpowers/plans/2026-10-11-template-branding-wave2.md
git commit -m "docs: activate template and branding wave"
```

### Task 2: Add The Author-State Brand Asset Contract

**Files:**
- Modify: `platform/contracts/survey-branding-v1.md`
- Modify: `platform/services/business/src/main/java/cn/mjy/platform/survey/SurveyAssetSource.java`
- Modify: `platform/services/business/src/main/java/cn/mjy/platform/asset/SurveyAssetLinks.java`
- Modify: `platform/services/business/src/test/java/cn/mjy/platform/asset/SurveyAssetPublishTest.java`
- Modify: `platform/tools/publish-gateway/pubgw/branding/schema.py`
- Modify: `platform/tools/publish-gateway/pubgw/branding/compile.py`
- Modify: `platform/tools/publish-gateway/tests/test_branding_schema.py`
- Modify: `platform/tools/publish-gateway/tests/test_branding_compile.py`

**Interfaces:**
- Consumes: existing `{assetId}` author reference, immutable `assetVersion`, signed asset fetch and `branding.logoFile`/`faviconFile` compiler contract.
- Produces: `branding.logoAsset` and `branding.faviconAsset` author-state objects; preview/publish materialization emits pinned, gateway-consumable assets and final safe filenames without accepting author-written URL/version.

- [x] **Step 1: Write failing Java asset-materialization tests**

Cover an active author image, archived image, foreign-tenant UUID, non-image asset, author-written `url`/`assetVersion`, Logo and favicon using distinct assets, and a second publish after adding an asset version. Assert each published definition records the version resolved for that publish.

- [x] **Step 2: Run the focused Java tests and confirm RED**

Run: `platform/deploy/platform-dev/mvn.sh test -Dtest='cn.mjy.platform.asset.SurveyAssetPublishTest'`

Expected: FAIL because branding-specific references are not compiled to local theme files.

- [x] **Step 3: Implement brand asset materialization**

Add a dedicated `SurveyAssetSource` brand-reference parser that only accepts `branding.logoAsset.assetId` and `branding.faviconAsset.assetId`; reuse tenant, origin, status and MIME checks from `SurveyAssetLinks`. Preserve generic question/theme asset behavior and reject definitions containing both legacy `logoFile` and new `logoAsset` for the same slot.

- [x] **Step 4: Write failing gateway schema/compiler tests**

Assert fixed filename derivation, allowed image MIME types, byte-size bounds, no path separators, no external URL, correct `mjybrandlogofile`/favicon output, and backward compatibility for existing valid `logoFile` definitions.

- [x] **Step 5: Implement gateway packaging and contract documentation**

Materialize brand bytes through the controlled asset channel into the generated survey theme files, derive filenames from trusted MIME type and immutable identity, and remove author-state fields from the engine-facing definition.

**Required boundary clarification:** the existing publish request carries only `definition`, so this step is not complete when the LSS contains only `./files/<name>`. Add a versioned, bounded `brandAssets` bundle to the platform publish and preview boundary. The platform must resolve each author `assetId` under tenant/origin/status/version checks, attach trusted `contentType`, `sizeBytes`, `sha256` and bytes, and sign the bundle identity. The gateway must verify the bundle against the materialized branding references, reject missing/mismatched/oversized bytes, and keep URL/version/MIME/size/asset IDs out of engine-facing output.

**Engine materialization ruling:** RemoteControl imports do not carry arbitrary theme archives, and `TemplateConfiguration` sanitizes image paths while importing. Therefore the gateway imports a definition without asset-file theme options, obtains the new `sid`, then calls a new fail-closed `MjyQuestionExtensions` signed direct endpoint. The endpoint accepts only a bounded, digest-verified bundle plus the gateway-compiled safe branding fields; it atomically writes fixed files under `upload/surveys/<sid>/images/` and updates only the `zh-business` per-survey options to `image::survey::<fixed-name>`. A channel or write failure must fail the publish/preview operation and use the existing survey rollback; no global theme mutation, arbitrary filesystem path, external URL, or unverified metadata is permitted. Reuse this same materialization path for preview; do not claim preview support from publish-only tests.

- [x] **Step 6: Run focused and complete backend gates**

Run: `platform/deploy/platform-dev/mvn.sh test -Dtest='cn.mjy.platform.asset.SurveyAssetPublishTest,cn.mjy.platform.survey.template.SurveyTemplateLifecycleTest'`

Run: `python3 -m unittest discover -s platform/tools/publish-gateway/tests -p 'test_branding_*.py' -v`

Expected: all focused tests pass and old filename-only fixtures remain compatible.

- [x] **Step 7: Commit**

```bash
git add platform/contracts/survey-branding-v1.md platform/services/business/src/main/java/cn/mjy/platform/survey/SurveyAssetSource.java platform/services/business/src/main/java/cn/mjy/platform/asset/SurveyAssetLinks.java platform/services/business/src/test/java/cn/mjy/platform/asset/SurveyAssetPublishTest.java platform/tools/publish-gateway/pubgw/branding platform/tools/publish-gateway/tests/test_branding_schema.py platform/tools/publish-gateway/tests/test_branding_compile.py
git commit -m "feat: pin brand assets in survey versions"
```

### Task 3: Add Typed Template, Asset, And Branding Clients

**Files:**
- Create: `platform/apps/admin-web/src/shared/api/templates.ts`
- Create: `platform/apps/admin-web/src/shared/api/templates.test.ts`
- Create: `platform/apps/admin-web/src/shared/api/assets.ts`
- Create: `platform/apps/admin-web/src/shared/api/assets.test.ts`
- Create: `platform/apps/admin-web/src/shared/api/branding.ts`
- Create: `platform/apps/admin-web/src/shared/api/branding.test.ts`

**Interfaces:**
- Consumes: existing template/asset endpoints, `getSurveyDraft`, `saveDraft`, and Task 2 brand asset shape.
- Produces: `listTemplates`, `getTemplate`, `copyTemplate`, lifecycle mutations, `listAssets`, `uploadAsset`, `parseBrandingDraft`, `mergeBrandingDraft`, and tenant-scoped query-key factories.

- [x] **Step 1: Write failing client and schema tests**

Cover every template scope/status, version/history item, optional review fields, platform versus tenant endpoints, multipart asset upload, image filtering, unknown/missing field rejection, query-key tenant isolation, and URL encoding.

- [x] **Step 2: Run focused tests and confirm RED**

Run: `npm --prefix platform/apps/admin-web test -- --run src/shared/api/templates.test.ts src/shared/api/assets.test.ts src/shared/api/branding.test.ts`

Expected: FAIL because the clients do not exist.

- [x] **Step 3: Implement template and asset clients**

Use existing `ApiClient`; schemas expose Java enum codes as lowercase typed values and reject malformed IDs, timestamps, version numbers and platform responses. `uploadAsset` accepts `name`, `File` and optional `AbortSignal` and sends `FormData` without setting a manual content type.

- [x] **Step 4: Implement brand draft parsing and merge**

`parseBrandingDraft(definition: unknown): BrandingFormState` must supply safe defaults without discarding unknown definition fields. `mergeBrandingDraft(definition, state): unknown` replaces only `theme`, `branding`, `additionalLanguages` and `translations`, fixes theme to `zh-business` when branding exists, and never writes `url` or `assetVersion`.

- [x] **Step 5: Run focused tests**

Run: `npm --prefix platform/apps/admin-web test -- --run src/shared/api/templates.test.ts src/shared/api/assets.test.ts src/shared/api/branding.test.ts`

Expected: PASS.

- [x] **Step 6: Commit**

```bash
git add platform/apps/admin-web/src/shared/api/templates.ts platform/apps/admin-web/src/shared/api/templates.test.ts platform/apps/admin-web/src/shared/api/assets.ts platform/apps/admin-web/src/shared/api/assets.test.ts platform/apps/admin-web/src/shared/api/branding.ts platform/apps/admin-web/src/shared/api/branding.test.ts
git commit -m "feat: add template and branding api clients"
```

### Task 4: Build Template Browsing And Copying

**Files:**
- Create: `platform/apps/admin-web/src/features/templates/TemplatesPage.tsx`
- Create: `platform/apps/admin-web/src/features/templates/TemplateDetailPage.tsx`
- Create: `platform/apps/admin-web/src/features/templates/TemplatesPage.test.tsx`
- Create: `platform/apps/admin-web/src/features/templates/templates.css`
- Modify: `platform/apps/admin-web/src/app/router.tsx`
- Modify: `platform/apps/admin-web/src/app/router.test.tsx`
- Modify: `platform/apps/admin-web/src/app/App.tsx`
- Modify: `platform/apps/admin-web/src/app/App.test.tsx`
- Modify: `platform/apps/admin-web/src/features/workspace/WorkspacePage.tsx`
- Modify: `platform/apps/admin-web/src/features/workspace/WorkspacePage.test.tsx`

**Interfaces:**
- Consumes: Task 3 read/copy APIs and existing resource tree/capability clients.
- Produces: `/templates`, `/templates/:templateId`, global “模板中心” navigation, and Workspace “从模板创建” command.

- [x] **Step 1: Write failing routing, navigation and page tests**

Cover default combined scope, platform/tenant filters, empty/loading/403/failure states, compact responsive rows, detail versions/history, published-only copy, folder selection, optional title, double-submit prevention, successful navigation to `/surveys/{id}/edit`, and mobile navigation closing after selection.

- [x] **Step 2: Run focused tests and confirm RED**

Run: `npm --prefix platform/apps/admin-web test -- --run src/features/templates/TemplatesPage.test.tsx src/app/router.test.tsx src/app/App.test.tsx src/features/workspace/WorkspacePage.test.tsx`

Expected: FAIL because routes and pages do not exist.

- [x] **Step 3: Implement routes, navigation and browse views**

Use a lazy route for both template pages, a `LayoutTemplate` Lucide icon, URL-backed `scope` and `view` filters, semantic table/list markup and Chinese status labels. Do not use a marketing card grid.

- [x] **Step 4: Implement copy flow and Workspace entry**

Reuse resource capabilities to allow only valid destination folders. On success invalidate resource/workspace/dashboard/template queries, then navigate to the independent survey draft while preserving `from=/templates/{id}`.

- [x] **Step 5: Run focused tests, lint and typecheck**

Run: `npm --prefix platform/apps/admin-web test -- --run src/features/templates/TemplatesPage.test.tsx src/app/router.test.tsx src/app/App.test.tsx src/features/workspace/WorkspacePage.test.tsx`

Run: `npm --prefix platform/apps/admin-web run lint`

Run: `npm --prefix platform/apps/admin-web run typecheck`

Expected: all commands pass.

- [x] **Step 6: Commit**

```bash
git add platform/apps/admin-web/src/features/templates platform/apps/admin-web/src/app/router.tsx platform/apps/admin-web/src/app/router.test.tsx platform/apps/admin-web/src/app/App.tsx platform/apps/admin-web/src/app/App.test.tsx platform/apps/admin-web/src/features/workspace/WorkspacePage.tsx platform/apps/admin-web/src/features/workspace/WorkspacePage.test.tsx
git commit -m "feat: add template browsing and copy flow"
```

### Task 5: Productize Tenant And Platform Template Lifecycles

**2026-10-27 用户确认的最小接口补充：**现有库列表只返回已发布项，审核队列只返回待审项，无法找回草稿、驳回或下架模板；资源能力接口不表达租户级模板审核权。本任务同步增加只读 `GET /v1/survey-templates/managed` 与 `GET /v1/survey-templates/{templateId}/capabilities`，复用现有成员校验、租户 RLS 和权限判定，不增加数据库迁移或新的写端点。对应变更在 `SurveyTemplateController`、`SurveyTemplateService`、`SurveyTemplateRepository`、`TemplateAccess`、`TemplateCapabilities`、`SurveyTemplateApiTest` 以及前端 `shared/api/templates.ts` 和测试中。

**Files:**
- Create: `platform/apps/admin-web/src/features/templates/TemplateActions.tsx`
- Create: `platform/apps/admin-web/src/features/templates/TemplateActions.test.tsx`
- Create: `platform/apps/admin-web/src/features/templates/PlatformTemplateImport.tsx`
- Modify: `platform/apps/admin-web/src/features/templates/TemplateDetailPage.tsx`
- Modify: `platform/apps/admin-web/src/features/templates/TemplatesPage.tsx`
- Modify: `platform/apps/admin-web/src/app/SurveyShell.tsx`
- Modify: `platform/apps/admin-web/src/app/SurveyShell.test.tsx`

**Interfaces:**
- Consumes: Task 3 lifecycle APIs, current auth roles, survey ID/draft version and Task 4 views.
- Produces: “保存为模板”、添加版本、送审、批准、驳回、下架，以及 `platform_operator` 可见的 platform definition file import/publish controls.

- [x] **Step 1: Write failing lifecycle tests**

Create a state/action matrix test for draft, pending, rejected, published and unpublished templates. Cover required rejection reason, 200/1000 character bounds, permission-denied controls, 409 refresh, mutation cache invalidation and repeated action blocking.

- [x] **Step 2: Run focused tests and confirm RED**

Run: `npm --prefix platform/apps/admin-web test -- --run src/features/templates/TemplateActions.test.tsx src/app/SurveyShell.test.tsx`

Expected: FAIL because lifecycle components and SurveyShell action do not exist.

- [x] **Step 3: Implement tenant lifecycle actions**

Derive allowed buttons from server state and capability responses, not role labels alone. Add “保存为模板” to SurveyShell commands and keep each mutation's pending/error/success status visible without losing page context.

- [x] **Step 4: Implement platform operator controls**

Only render for an exact `platform_operator` role. Accept a local `.json` file, parse and validate it before calling `/v1/platform/survey-templates`; never expose a raw HTML/JS editor. Publishing requires an explicit version selection and confirmation.

- [x] **Step 5: Run focused and complete frontend tests**

Run: `npm --prefix platform/apps/admin-web test -- --run src/features/templates/TemplateActions.test.tsx src/features/templates/TemplatesPage.test.tsx src/app/SurveyShell.test.tsx`

Expected: PASS.

- [x] **Step 6: Commit**

```bash
git add platform/apps/admin-web/src/features/templates platform/apps/admin-web/src/app/SurveyShell.tsx platform/apps/admin-web/src/app/SurveyShell.test.tsx
git commit -m "feat: add template lifecycle controls"
```

### Task 6: Build The Brand And Language Editor

**Files:**
- Create: `platform/apps/admin-web/src/features/branding/BrandingPage.tsx`
- Create: `platform/apps/admin-web/src/features/branding/BrandingPage.test.tsx`
- Create: `platform/apps/admin-web/src/features/branding/AssetPicker.tsx`
- Create: `platform/apps/admin-web/src/features/branding/LanguageEditor.tsx`
- Create: `platform/apps/admin-web/src/features/branding/branding.css`
- Modify: `platform/apps/admin-web/src/app/router.tsx`
- Modify: `platform/apps/admin-web/src/app/SurveyShell.tsx`
- Modify: `platform/apps/admin-web/src/app/SurveyShell.test.tsx`
- Modify: `platform/apps/admin-web/src/shared/api/schemas.ts`
- Modify: `platform/apps/admin-web/src/shared/api/branding.ts`
- Modify: `platform/apps/admin-web/src/shared/api/branding.test.ts`
- Modify: `platform/apps/admin-web/src/shared/api/dashboard.test.ts`
- Modify: `platform/apps/admin-web/src/features/dashboard/DashboardPage.tsx`
- Modify: `platform/services/business/src/main/java/cn/mjy/platform/dashboard/DashboardPage.java`
- Modify: `platform/services/business/src/main/java/cn/mjy/platform/dashboard/DashboardRecentWorkRepository.java`
- Create: `platform/services/business/src/main/resources/db/migration/V934__dashboard_branding_recent_work.sql`
- Modify: `platform/services/business/src/test/java/cn/mjy/platform/dashboard/DashboardRecentWorkRepositoryTest.java`
- Modify: `platform/services/business/src/test/java/cn/mjy/platform/dashboard/DashboardRecentWorkTenantIsolationTest.java`

**Interfaces:**
- Consumes: Task 3 draft/brand/asset clients, resource `canEdit`, and SurveyShell unsaved-state contract.
- Produces: `/surveys/:surveyId/branding`, brand asset upload/selection, localized title/footer and structured translations, and cache-safe draft saving.

- [x] **Step 1: Write failing page and form tests**

Cover parse from existing draft, `#rgb`/`#rrggbb`, Logo alt requirements, active image filtering, upload failure/retry, base and additional language validation, removal confirmation, translation target validation, read-only mode, dirty navigation block, beforeunload, successful version/cache synchronization, 409 conflict and preservation of unrelated draft fields.

- [x] **Step 2: Run focused tests and confirm RED**

Run: `npm --prefix platform/apps/admin-web test -- --run src/features/branding/BrandingPage.test.tsx src/app/SurveyShell.test.tsx`

Expected: FAIL because the brand route and components do not exist.

- [x] **Step 3: Implement the brand form and asset picker**

Use React Hook Form with controlled color text/native color input, accessible upload/select/remove controls and `aria-describedby` errors. Save only through `saveDraft(api, surveyId, version, mergeBrandingDraft(...))`.

- [x] **Step 4: Implement the structured language editor**

Read group/question/subquestion/answer identities from the current draft; store only supported translation fields. Missing entries remain absent so the gateway performs base-language fallback.

- [x] **Step 5: Extend the recent-work contract for branding**

Add `DashboardPage.BRANDING("branding")`, target `/surveys/{id}/branding`, and migration `V934` that replaces the page check constraint with the old values plus `branding` while preserving the `version_no` invariant. Extend Java repository/isolation tests and frontend Zod/client tests before wiring `useSurveyPageReady('branding', ready)`; keep versions/publish active-tab behavior unchanged.

- [x] **Step 6: Run focused tests, full frontend tests and build**

Run: `platform/deploy/platform-dev/mvn.sh test -Dtest='cn.mjy.platform.dashboard.DashboardRecentWorkRepositoryTest,cn.mjy.platform.dashboard.DashboardRecentWorkTenantIsolationTest'`

Run: `npm --prefix platform/apps/admin-web test -- --run src/features/branding/BrandingPage.test.tsx src/app/SurveyShell.test.tsx src/shared/api/dashboard.test.ts`

Run: `npm --prefix platform/apps/admin-web test -- --run`

Run: `npm --prefix platform/apps/admin-web run build`

Expected: all commands pass.

- [x] **Step 7: Commit**

```bash
git add platform/apps/admin-web/src/features/branding platform/apps/admin-web/src/features/dashboard/DashboardPage.tsx platform/apps/admin-web/src/app/router.tsx platform/apps/admin-web/src/app/SurveyShell.tsx platform/apps/admin-web/src/app/SurveyShell.test.tsx platform/apps/admin-web/src/shared/api/branding.ts platform/apps/admin-web/src/shared/api/branding.test.ts platform/apps/admin-web/src/shared/api/schemas.ts platform/apps/admin-web/src/shared/api/dashboard.test.ts platform/services/business/src/main/java/cn/mjy/platform/dashboard/DashboardPage.java platform/services/business/src/main/java/cn/mjy/platform/dashboard/DashboardRecentWorkRepository.java platform/services/business/src/main/resources/db/migration/V934__dashboard_branding_recent_work.sql platform/services/business/src/test/java/cn/mjy/platform/dashboard/DashboardRecentWorkRepositoryTest.java platform/services/business/src/test/java/cn/mjy/platform/dashboard/DashboardRecentWorkTenantIsolationTest.java
git commit -m "feat: add brand and language editor"
```

### Task 7: Prove The Real Template-To-Branded-Response Journey

**Files:**
- Create: `platform/apps/admin-web/e2e/template-branding.spec.ts`
- Modify: `platform/deploy/test/run-admin-web-e2e.sh`
- Modify: `platform/tests/e2e/admin_web_gate.py`
- Modify: `platform/tests/e2e/test_admin_web_gate.py`
- Modify as required: `platform/deploy/test/admin-web-e2e.compose.yml`

**Interfaces:**
- Consumes: Tasks 2-6 and the existing real preview/publish/respondent stack.
- Produces: deterministic browser evidence for template creation/review/copy, brand persistence, preview, publish, response, asset-version pinning, tenant isolation and responsive/accessibility states.

- [x] **Step 1: Write failing E2E evidence assertions**

Require named scenarios for tenant template lifecycle, platform template copy, brand save/refresh, preview branding, respondent language switch, no third-party absolute URL, old published asset version, 409 conflict, forbidden tenant/platform action, keyboard flow and 819/820/1179/1180 widths.

- [x] **Step 2: Run the browser gate test and confirm RED**

Run: `python3 platform/tests/e2e/test_admin_web_gate.py`

Expected: FAIL because the Wave 2 scenarios are not declared.

- [x] **Step 3: Implement the Playwright journey and deterministic fixtures**

Use real platform and LimeSurvey services. Fixture credentials stay in ignored mode-0600 runtime files; tests must not log tokens, passwords or signed asset URLs.

- [x] **Step 4: Run the complete real E2E gate**

Run: `platform/deploy/test/run-admin-web-e2e.sh`

Expected: all Wave 1 and Wave 2 scenarios pass, screenshots show non-overlapping desktop/mobile layouts, and external URL scan is empty.

2026-10-27 用户授权接续后，保留网关现场确认最终 502 为 apply 阶段主题回退：冷引擎未注册 zh-business。复用既有安装器后预览成功；语言验收改为实际题干译文，避免把预览恢复标记或未展示的说明当作可见标题。补齐正式答卷、第二版发布、旧版图片 SHA-256 固定、网站图标上传和实际第三方请求断言，并修复品牌保存后的发布概览缓存及第二次发布等待时序。MySQL/PostgreSQL 均 19/19，四层交叉核验通过；门禁单元 48、前端 353、网关 1235、隔离 Java 1494/184 类及管理端静态/构建门通过。实现提交 1ed42c34a2、9b059a7523、c80e9b406b；Task 8 仍未完成，整体能力状态不提升。

- [x] **Step 5: Commit**

```bash
git add platform/apps/admin-web/e2e/template-branding.spec.ts platform/deploy/test/run-admin-web-e2e.sh platform/tests/e2e/admin_web_gate.py platform/tests/e2e/test_admin_web_gate.py platform/deploy/test/admin-web-e2e.compose.yml
git commit -m "test: cover template and branding journey"
```

### Task 8: Run Production Gates And Synchronize Delivery Evidence

**Files:**
- Modify: `platform/docs/productization/frontend-backend-requirements.md`
- Modify: `platform/docs/productization/product-blueprint.md`
- Modify: `platform/docs/productization/release-roadmap.md`
- Modify: `platform/docs/productization/capability-matrix.yaml`
- Modify: `platform/README.md`
- Modify: `docs/agent/HARNESS_DELIVERY.json`
- Modify: `docs/agent/PROGRESS.md`
- Modify: `docs/agent/PROJECT_MEMORY.md`
- Create: `docs/agent/reviews/2026-10-11-wave2-security.md`
- Create: `docs/agent/reviews/2026-10-11-wave2-dx-ci.md`
- Create: `docs/agent/reviews/2026-10-11-wave2-whole-branch.md`

**Interfaces:**
- Consumes: all implementation commits, Harness run, real E2E output and three independent reviews.
- Produces: six-layer `templates-branding` evidence, production Compose smoke result, GitHub traceability and Obsidian project update.

- [ ] **Step 1: Run complete repository gates**

Run: `platform/deploy/test/run-platform-tests.sh`

Run: `npm --prefix platform/apps/admin-web test -- --run`

Run: `npm --prefix platform/apps/admin-web run lint`

Run: `npm --prefix platform/apps/admin-web run typecheck`

Run: `npm --prefix platform/apps/admin-web run build`

Run: `python3 -m unittest discover -s platform/tools/publish-gateway/tests -v`

Run: `scripts/agent-harness gate --run-id <wave2-run-id>`

Expected: zero failure, error or required skip; every evidence item binds to the same implementation HEAD.

- [ ] **Step 2: Run production Compose smoke**

Install or upgrade a disposable single-host environment, execute the Task 7 journey through the Caddy entrypoint, run `surveyctl doctor`, then verify backup and restore preserve templates, brand assets, published versions and responses.

- [ ] **Step 3: Perform three independent reviews**

Use distinct agents for security, DX/CI and whole-branch review, selecting a model appropriate to each review's risk. Each report records the actual model and reasoning effort, binds the exact implementation commit/range, and ends with canonical `SPEC_COMPLIANCE=APPROVED` and `CODE_QUALITY=APPROVED`; any finding returns to its owning task and invalidates affected evidence.

- [ ] **Step 4: Update product and Harness documentation**

Mark only proven capabilities complete. Record commands, HEAD, scenario counts, remaining external blockers and the next Wave; do not copy secrets, raw agent logs or signed URLs.

- [ ] **Step 5: Commit, push, verify GitHub, and update Obsidian**

```bash
git add platform/docs/productization platform/README.md docs/agent
git commit -m "docs: record template and branding delivery"
git push
```

After hosted checks pass, update the registered Obsidian project overview with date, branch, commit, tests, delivered scope, remaining issues and GitHub traceability.

## Final Acceptance

- [ ] Every child Agent run records the actual runtime model and reasoning effort, and no run silently falls back after an availability failure.
- [ ] Dashboard Task 7 and its repository/knowledge-base sync are closed before Wave 2 activation.
- [ ] A tenant author can create, submit, review and copy a template without entering LimeSurvey admin.
- [ ] A platform operator can publish a platform template; a normal tenant can copy but cannot manage it.
- [ ] Brand color, Logo, favicon, title, footer and at least one additional language survive save and refresh.
- [ ] Real preview and respondent pages render the configured brand and make no third-party request.
- [ ] Published version 1 keeps its original asset bytes after a new asset version is uploaded and version 2 is published.
- [ ] Template copies contain no participant, policy, password hash, administrator email or shared entity UUID.
- [ ] Conflict, forbidden, invalid translation, archived asset and unavailable asset-service paths fail closed with Chinese user-facing recovery guidance.
- [ ] Full Java, Python, TypeScript, browser and production Compose gates pass with no required skip.
- [ ] Requirements, blueprint, roadmap, capability matrix, README, Harness, GitHub and Obsidian all point to the same verified implementation commit.
