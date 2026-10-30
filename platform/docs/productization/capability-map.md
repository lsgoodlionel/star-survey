# 产品化能力基线

> 本文件由 `platform/tools/productization/render_capabilities.py` 根据 `capabilities.json` 生成，请勿手工编辑。

产品完成必须具备需求、后端、前端、流程、生产和追溯六层证据。后端完成不等于产品验收通过。

## 汇总

- 能力项：12
- 产品验收通过：0
- 部分完成：11
- 未开始：1
- 外部依赖：0

## 能力矩阵

| 能力 | 需求编号 | 后端 | 前端 | 流程 | 生产 | 总体状态 | 下一波次 |
|---|---|---|---|---|---|---|---|
| 管理 | `R19-01`, `R19-03`, `R20-01`, `R20-02`, `R20-03`, `R22-01`, `R22-11`, `R22-12` | 部分完成 | 未开始 | 未开始 | 未开始 | 部分完成 | Wave 6 |
| 审批与发布 | `R01-07`, `R01-09` | 完成 | 完成 | 完成 | 未开始 | 部分完成 | Wave 1 |
| 通讯录与投放 | `R05-04`, `R05-05`, `R05-06`, `R18-01`, `R18-02`, `R18-03`, `R18-06` | 部分完成 | 部分完成 | 部分完成 | 未开始 | 部分完成 | Wave 4 |
| 投放链接与二维码 | `R05-01`, `R05-07`, `R05-08` | 完成 | 完成 | 完成 | 未开始 | 部分完成 | Wave 1 |
| 编辑 | `R01-04`, `R01-05`, `R01-08` | 完成 | 部分完成 | 部分完成 | 未开始 | 部分完成 | Wave 5 |
| 导出 | `R06-02`, `R06-03`, `R06-04`, `R06-05`, `R06-06`, `R06-07` | 部分完成 | 完成 | 部分完成 | 未开始 | 部分完成 | Wave 3 |
| 导入 | `R01-02` | 完成 | 完成 | 完成 | 未开始 | 部分完成 | Wave 5 |
| 预览 | `R01-07` | 完成 | 完成 | 完成 | 未开始 | 部分完成 | Wave 1 |
| 生产部署 | `R20-11`, `R23-08`, `R23-09`, `R23-10`, `R23-11`, `R23-12` | 部分完成 | 未开始 | 未开始 | 未开始 | 未开始 | Wave 0 |
| 答卷与摘要 | `R06-01`, `R19-02` | 完成 | 完成 | 完成 | 未开始 | 部分完成 | Wave 3 |
| 模板与品牌 | `R01-03`, `R01-06`, `R19-05`, `R19-06`, `R19-10` | 完成 | 完成 | 完成 | 部分完成 | 部分完成 | Wave 2 |
| 工作台 | `R01-01`, `R19-04` | 完成 | 完成 | 完成 | 未开始 | 部分完成 | Wave 6 |

## 证据

### 管理 (`administration`)

- **后端** （实现）[`platform/services/business/src/main/java/cn/mjy/platform/tenant/TenantAdminController.java`](../../services/business/src/main/java/cn/mjy/platform/tenant/TenantAdminController.java) 定位 `public class TenantAdminController`：租户生命周期管理后端接口已存在。
- **后端** （实现）[`platform/services/business/src/main/java/cn/mjy/platform/tenant/engine/EngineInstanceAdminController.java`](../../services/business/src/main/java/cn/mjy/platform/tenant/engine/EngineInstanceAdminController.java) 定位 `public class EngineInstanceAdminController`：引擎实例运营接口已存在。
- **前端** （审计）[`docs/audits/2026-10-09-product-alignment/14-full-development-report.md`](../../../docs/audits/2026-10-09-product-alignment/14-full-development-report.md) 定位 `**运营后台**`：核查报告记录运营后台仍待产品化。
- **需求** （需求规格）[`platform/docs/traceability/requirement-index.md`](../traceability/requirement-index.md) 定位 `| R19-01 |`：多用户与项目分配需求已编号。

### 审批与发布 (`approval-publish`)

- **后端** （实现）[`platform/services/business/src/main/java/cn/mjy/platform/survey/PublishApprovalController.java`](../../services/business/src/main/java/cn/mjy/platform/survey/PublishApprovalController.java) 定位 `public class PublishApprovalController`：审批状态机与发布接口已存在。
- **前端** （实现）[`platform/apps/admin-web/src/features/publish/PublishPage.tsx`](../../apps/admin-web/src/features/publish/PublishPage.tsx) 定位 `export function PublishRoutePage`：审批、发布状态和版本入口已接入。
- **需求** （需求规格）[`platform/docs/traceability/requirement-index.md`](../traceability/requirement-index.md) 定位 `| R01-09 |`：发布审核需求已编号。
- **追溯** （追溯记录）[`platform/docs/traceability/requirement-tests.md`](../traceability/requirement-tests.md) 定位 `| R01-09 | 集成 |`：审批绑定版本和绕过防护证据已登记。

### 通讯录与投放 (`contacts-delivery`)

- **后端** （实现）[`platform/services/business/src/main/java/cn/mjy/platform/contacts/ContactController.java`](../../services/business/src/main/java/cn/mjy/platform/contacts/ContactController.java) 定位 `class ContactController`：联系人、部门、标签和名单后端已形成。
- **后端** （实现）[`platform/services/business/src/main/java/cn/mjy/platform/delivery/DeliveryRulesController.java`](../../services/business/src/main/java/cn/mjy/platform/delivery/DeliveryRulesController.java) 定位 `setReminderRuleEnabled`：批量任务、验签去重回执、不可翻转退订、完成者抑制和催答上限后端已形成；渠道可用性按租户失败即关闭。
- **后端** （自动化测试）[`platform/services/business/src/test/java/cn/mjy/platform/delivery/DeliveryApiTest.java`](../../services/business/src/test/java/cn/mjy/platform/delivery/DeliveryApiTest.java) 定位 `anOwnerCanCreateListAndToggleTheSurveysReminderRule`：测试源码 FakeEmailProvider 验证 audience 任务成功投递、脱敏任务发现和催答规则启停；不属于生产 provider 证据。
- **流程** （浏览器测试）[`platform/apps/admin-web/e2e/authoring.spec.ts`](../../apps/admin-web/e2e/authoring.spec.ts) 定位 `exerciseDeliveryWorkspace`：2026-10-30 隔离 Docker MySQL/PostgreSQL 各 20/20：一级投放工作区完成名单、部门、标签、联系人、标签分配和导入任务，再贯通发布、链接/二维码、audience、未配置渠道 503、正式作答与导出。
- **前端** （实现）[`platform/apps/admin-web/src/features/audience/AudiencePage.tsx`](../../apps/admin-web/src/features/audience/AudiencePage.tsx) 定位 `export function AudiencePage`：问卷内页面支持 audience、链接/二维码入口、批量任务、中文逐收件人状态、催答次数、已认证回执与退订/完成抑制状态。
- **前端** （实现）[`platform/apps/admin-web/src/features/delivery/DeliveryWorkspacePage.tsx`](../../apps/admin-web/src/features/delivery/DeliveryWorkspacePage.tsx) 定位 `export function DeliveryWorkspacePage`：正式一级投放工作区支持名单、部门、标签、联系人、停用、标签分配及 CSV/JSON 导入任务，并进入问卷内受众投放上下文。
- **生产** （部署基线）[`platform/deploy/production/compose.yml`](../../deploy/production/compose.yml) 定位 `PLATFORM_DELIVERY_SECRET`：生产 compose 与安装器已具备投递主密钥 secret 和默认关闭的 worker 开关；网易 163 已在本地 Docker 完成真实 SMTP 提交，正式生产环境仍待 Wave 6 验收。
- **需求** （需求规格）[`platform/docs/traceability/requirement-index.md`](../traceability/requirement-index.md) 定位 `| R18-01 |`：通讯录导入与同步需求已编号。
- **追溯** （追溯记录）[`docs/superpowers/plans/2026-10-28-wave4-audience-delivery.md`](../../../docs/superpowers/plans/2026-10-28-wave4-audience-delivery.md) 定位 `2026-10-30 阶段 9：正式投放工作区与 Wave 4 本地准出`：Wave 4 蓝图本地功能准出、双库浏览器、真实 SMTP 与明确延期边界已记录。

### 投放链接与二维码 (`delivery-links`)

- **后端** （实现）[`platform/services/business/src/main/java/cn/mjy/platform/delivery/DeliveryLinkController.java`](../../services/business/src/main/java/cn/mjy/platform/delivery/DeliveryLinkController.java) 定位 `public class DeliveryLinkController`：投放链接、短链和二维码后端接口已存在。
- **流程** （浏览器测试）[`platform/apps/admin-web/e2e/authoring.spec.ts`](../../apps/admin-web/e2e/authoring.spec.ts) 定位 `decodeQrImage`：真实浏览器确认二维码图像完成加载，用 jsQR 解码渲染像素并逐字比对新建链接 URL，再完成正式作答。
- **前端** （实现）[`platform/apps/admin-web/src/features/publish/PublishedAccessPanel.tsx`](../../apps/admin-web/src/features/publish/PublishedAccessPanel.tsx) 定位 `export function PublishedAccessPanel`：发布后可创建投放链接、展示后端二维码并进入答卷工作区。
- **需求** （需求规格）[`platform/docs/traceability/requirement-index.md`](../traceability/requirement-index.md) 定位 `| R05-01 |`：链接、二维码和短链需求已编号。
- **追溯** （追溯记录）[`platform/docs/traceability/requirement-tests.md`](../traceability/requirement-tests.md) 定位 `| R05-01 | 端到端 |`：链接、二维码和正式作答证据已登记。

### 编辑 (`editing`)

- **后端** （实现）[`platform/services/business/src/main/java/cn/mjy/platform/survey/SurveyController.java`](../../services/business/src/main/java/cn/mjy/platform/survey/SurveyController.java) 定位 `public class SurveyController`：草稿读取、保存和版本控制接口已存在。
- **流程** （审计）[`docs/audits/2026-10-09-product-alignment/14-full-development-report.md`](../../../docs/audits/2026-10-09-product-alignment/14-full-development-report.md) 定位 `#### Step 3：问卷编辑`：核查报告确认基础作者链路可用，同时记录 Phase C 缺口。
- **前端** （实现）[`platform/apps/admin-web/src/features/editor/EditorPage.tsx`](../../apps/admin-web/src/features/editor/EditorPage.tsx) 定位 `export function EditorRoutePage`：基础题型编辑、排序和保存已接入；高级题型仍只读。
- **需求** （需求规格）[`platform/docs/traceability/requirement-index.md`](../traceability/requirement-index.md) 定位 `| R01-04 |`：基础编辑需求已编号。

### 导出 (`exports`)

- **后端** （实现）[`platform/services/business/src/main/java/cn/mjy/platform/response/PdfExportWriter.java`](../../services/business/src/main/java/cn/mjy/platform/response/PdfExportWriter.java) 定位 `final class PdfExportWriter`：逐份答卷 PDF 使用随包中文字体和 ToUnicode 映射生成可复制中文；最终资源预算回归仍待收口。
- **后端** （实现）[`platform/services/business/src/main/java/cn/mjy/platform/response/ResponseExportController.java`](../../services/business/src/main/java/cn/mjy/platform/response/ResponseExportController.java) 定位 `public class ResponseExportController`：导出作业创建、进度、下载和再次授权接口已存在。
- **后端** （实现）[`platform/services/business/src/main/java/cn/mjy/platform/response/StatisticalDocxExportWriter.java`](../../services/business/src/main/java/cn/mjy/platform/response/StatisticalDocxExportWriter.java) 定位 `final class StatisticalDocxExportWriter`：Word 统计报告按冻结字段输出有效值、空值、频数和比例；最终资源预算回归仍待收口。
- **后端** （实现）[`platform/services/business/src/main/java/cn/mjy/platform/response/WordTemplateController.java`](../../services/business/src/main/java/cn/mjy/platform/response/WordTemplateController.java) 定位 `class WordTemplateController`：自定义 Word 模板提供租户与问卷隔离的上传、不可变版本和服务端冻结接口；最终安全回归仍待收口。
- **流程** （浏览器测试）[`platform/apps/admin-web/e2e/authoring.spec.ts`](../../apps/admin-web/e2e/authoring.spec.ts) 定位 `assertDownloadedCsvContainsFormalResponse`：真实浏览器解包下载 ZIP，校验 CSV UTF-8 BOM/CRLF，并通过 fields.csv 字段字典确认唯一正式答卷的真实字段值为 A1；其他格式继续由专项测试覆盖。
- **流程** （浏览器测试）[`platform/apps/admin-web/e2e/template-branding.spec.ts`](../../apps/admin-web/e2e/template-branding.spec.ts) 定位 `自定义 Word 模板`：2026-10-30 当前工作区隔离 MySQL/PostgreSQL 各 5/5，覆盖七种标准格式及自定义模板拒绝、上传、版本固定、真实替换和制品解包；跨层门禁通过。
- **前端** （实现）[`platform/apps/admin-web/src/features/responses/ResponsesPage.tsx`](../../apps/admin-web/src/features/responses/ResponsesPage.tsx) 定位 `创建导出任务`：前端提供真实支持格式选择、任务状态、取消、重试和下载。
- **需求** （需求规格）[`platform/docs/traceability/requirement-index.md`](../traceability/requirement-index.md) 定位 `| R06-02 |`：Excel 和 CSV 导出需求已编号。
- **追溯** （追溯记录）[`platform/docs/traceability/requirement-tests.md`](../traceability/requirement-tests.md) 定位 `| R06-02 | 端到端 |`：CSV 创建、完成和下载的完整旅程证据已登记。

### 导入 (`import`)

- **后端** （实现）[`platform/services/business/src/main/java/cn/mjy/platform/survey/SurveyController.java`](../../services/business/src/main/java/cn/mjy/platform/survey/SurveyController.java) 定位 `@PostMapping("/{id}/import/preview")`：导入预览与提交接口已存在。
- **前端** （实现）[`platform/apps/admin-web/src/features/import/ImportPage.tsx`](../../apps/admin-web/src/features/import/ImportPage.tsx) 定位 `export function ImportRoutePage`：导入页面支持预览、异常行和选择性导入。
- **需求** （需求规格）[`platform/docs/traceability/requirement-index.md`](../traceability/requirement-index.md) 定位 `| R01-02 |`：批量文本导入需求已编号。
- **追溯** （追溯记录）[`platform/docs/traceability/requirement-tests.md`](../traceability/requirement-tests.md) 定位 `| R01-02 | 单测 |`：后端导入行为已有登记证据。

### 预览 (`preview`)

- **后端** （实现）[`platform/services/business/src/main/java/cn/mjy/platform/survey/preview/PreviewSessionController.java`](../../services/business/src/main/java/cn/mjy/platform/survey/preview/PreviewSessionController.java) 定位 `public class PreviewSessionController`：隔离预览 session 的创建、查询和显式结束接口已交付。
- **流程** （浏览器测试）[`platform/apps/admin-web/e2e/authoring.spec.ts`](../../apps/admin-web/e2e/authoring.spec.ts) 定位 `readPreviewIsolationSnapshot`：真实预览事件投递后直接比较正式 version、binding、route、outbox 和 response 计数，五项前后均为零。
- **前端** （实现）[`platform/apps/admin-web/src/features/preview/PreviewPage.tsx`](../../apps/admin-web/src/features/preview/PreviewPage.tsx) 定位 `export function PreviewRoutePage`：页面同时提供快速预览和隔离真实预览；轮询失败可恢复，终态可新建且保留旧 session 审计。
- **需求** （需求规格）[`platform/docs/traceability/requirement-index.md`](../traceability/requirement-index.md) 定位 `| R01-07 |`：预览、发布与关闭为复合需求。
- **追溯** （追溯记录）[`platform/docs/traceability/requirement-tests.md`](../traceability/requirement-tests.md) 定位 `| R01-07 | 端到端 |`：真实预览、关闭和正式计数隔离证据已登记。

### 生产部署 (`production-deployment`)

- **后端** （实现）[`platform/deploy/private/docker-compose.private.yml`](../../deploy/private/docker-compose.private.yml) 定位 `services:`：现有私有开发部署提供基础，但不是 Production Compose。
- **生产** （审计）[`platform/README.md`](../../README.md) 定位 `当前仍不能宣称正式 production-ready`：仓库状态明确记录生产代码已存在，但真实 Release 和四平台矩阵仍未完成。
- **需求** （需求规格）[`platform/docs/traceability/requirement-index.md`](../traceability/requirement-index.md) 定位 `| R20-11 |`：本地私有化部署需求已编号。
- **追溯** （追溯记录）[`docs/audits/2026-10-09-product-alignment/14-full-development-report.md`](../../../docs/audits/2026-10-09-product-alignment/14-full-development-report.md) 定位 `生产部署路径、TLS、mTLS`：核查报告记录生产部署和供应链门禁尚未闭环。

### 答卷与摘要 (`responses-summary`)

- **后端** （实现）[`platform/services/business/src/main/java/cn/mjy/platform/response/ResponseQueryController.java`](../../services/business/src/main/java/cn/mjy/platform/response/ResponseQueryController.java) 定位 `public class ResponseQueryController`：跨版本答卷查询、摘要和字段接口已存在。
- **后端** （自动化测试）[`platform/services/business/src/test/java/cn/mjy/platform/response/ResponseTenantIsolationTest.java`](../../services/business/src/test/java/cn/mjy/platform/response/ResponseTenantIsolationTest.java) 定位 `class ResponseTenantIsolationTest`：答卷租户隔离已有后端测试。
- **流程** （浏览器测试）[`platform/apps/admin-web/e2e/authoring.spec.ts`](../../apps/admin-web/e2e/authoring.spec.ts) 定位 `刷新答卷数据`：正式作答后不使用固定 sleep，通过有界 UI 刷新 eventual 验证一份完成答卷可见。
- **前端** （实现）[`platform/apps/admin-web/src/features/responses/ResponsesPage.tsx`](../../apps/admin-web/src/features/responses/ResponsesPage.tsx) 定位 `刷新答卷数据`：答卷摘要与明细设为即时过期查询，并提供常显手动刷新及中文进度反馈。
- **需求** （需求规格）[`platform/docs/traceability/requirement-index.md`](../traceability/requirement-index.md) 定位 `| R06-01 |`：答卷查询需求已编号。
- **追溯** （追溯记录）[`platform/docs/traceability/requirement-tests.md`](../traceability/requirement-tests.md) 定位 `| R06-01 | 端到端 |`：正式答卷投影、摘要和明细可见证据已登记。

### 模板与品牌 (`templates-branding`)

- **后端** （实现）[`platform/contracts/survey-branding-v1.md`](../../contracts/survey-branding-v1.md) 定位 `# 问卷品牌与多语言 v1`：品牌与多语言已定义契约。
- **后端** （实现）[`platform/services/business/src/main/java/cn/mjy/platform/survey/template/SurveyTemplateController.java`](../../services/business/src/main/java/cn/mjy/platform/survey/template/SurveyTemplateController.java) 定位 `class SurveyTemplateController`：租户模板生命周期后端接口已存在。
- **流程** （浏览器测试）[`platform/apps/admin-web/e2e/template-branding.spec.ts`](../../apps/admin-web/e2e/template-branding.spec.ts) 定位 `branded copy previews and publishes with language and pinned image bytes`：2026-10-28 本地 Docker MySQL 与 PostgreSQL 各 19/19 的 Wave 2 真实浏览器旅程覆盖模板、品牌预览、正式作答和素材版本固定，按用户口径完成本地功能验收；正式 Release 和生产部署另行验收。
- **前端** （实现）[`platform/apps/admin-web/src/features/branding/BrandingPage.tsx`](../../apps/admin-web/src/features/branding/BrandingPage.tsx) 定位 `export function BrandingPage()`：Wave 2 品牌与语言编辑入口已实现；本能力映射的封面、背景、水印等更广需求尚未全部验收。
- **需求** （需求规格）[`platform/docs/traceability/requirement-index.md`](../traceability/requirement-index.md) 定位 `| R19-05 |`：企业模板库需求已编号。
- **追溯** （追溯记录）[`docs/agent/PROGRESS.md`](../../../docs/agent/PROGRESS.md) 定位 `Task 8: complete`：Wave 2 本地功能切片验收、双库浏览器及 GitHub CI 证据已登记；更广需求和生产层仍保持部分完成。

### 工作台 (`workspace`)

- **后端** （实现）[`platform/services/business/src/main/java/cn/mjy/platform/dashboard/DashboardController.java`](../../services/business/src/main/java/cn/mjy/platform/dashboard/DashboardController.java) 定位 `public class DashboardController`：Dashboard 在一个授权快照中汇总摘要、任务、问卷和最近工作；资源树接口继续服务完整项目与问卷管理。
- **后端** （自动化测试）[`platform/services/business/src/test/java/cn/mjy/platform/dashboard/DashboardRecentWorkTenantIsolationTest.java`](../../services/business/src/test/java/cn/mjy/platform/dashboard/DashboardRecentWorkTenantIsolationTest.java) 定位 `class DashboardRecentWorkTenantIsolationTest`：集成测试覆盖最近工作在跨租户、归档和失权场景下的过滤。
- **后端** （自动化测试）[`platform/services/business/src/test/java/cn/mjy/platform/dashboard/DashboardTenantIsolationTest.java`](../../services/business/src/test/java/cn/mjy/platform/dashboard/DashboardTenantIsolationTest.java) 定位 `class DashboardTenantIsolationTest`：集成测试覆盖角色可见性、租户隔离和固定查询预算。
- **流程** （审计）[`docs/audits/2026-10-09-product-alignment/15-business-dashboard-v1-follow-up.md`](../../../docs/audits/2026-10-09-product-alignment/15-business-dashboard-v1-follow-up.md) 定位 `## 2. 已被新证据取代的结论`：跟进审计明确列出 2026-10-09 基线中已被 Dashboard v1 和发布后闭环取代的判断。
- **流程** （浏览器测试）[`platform/apps/admin-web/e2e/dashboard.spec.ts`](../../apps/admin-web/e2e/dashboard.spec.ts) 定位 `pending approval is visible only to a real publish reviewer`：真实栈验证登录落 Dashboard、发布与答卷往返、重新认证后的最近工作、真实项目级角色隔离、移动导航及 819/820/1179/1180px DOM 几何。
- **前端** （实现）[`platform/apps/admin-web/src/features/dashboard/DashboardPage.tsx`](../../apps/admin-web/src/features/dashboard/DashboardPage.tsx) 定位 `export function DashboardPage`：登录首页展示授权摘要、待办、问卷快捷操作和可恢复的最近工作；完整资源管理保留在项目与问卷页。
- **需求** （需求规格）[`platform/docs/traceability/requirement-index.md`](../traceability/requirement-index.md) 定位 `| R01-01 |`：空白与应用类型创建需求已编号。
