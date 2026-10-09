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
| 通讯录与投放 | `R05-04`, `R05-05`, `R05-06`, `R18-01`, `R18-02`, `R18-03`, `R18-06` | 部分完成 | 未开始 | 未开始 | 未开始 | 部分完成 | Wave 4 |
| 投放链接与二维码 | `R05-01`, `R05-07`, `R05-08` | 完成 | 未开始 | 未开始 | 未开始 | 部分完成 | Wave 1 |
| 编辑 | `R01-04`, `R01-05`, `R01-08` | 完成 | 部分完成 | 部分完成 | 未开始 | 部分完成 | Wave 5 |
| 导出 | `R06-02`, `R06-03`, `R06-05`, `R06-06`, `R06-07` | 部分完成 | 未开始 | 未开始 | 未开始 | 部分完成 | Wave 3 |
| 导入 | `R01-02` | 完成 | 完成 | 完成 | 未开始 | 部分完成 | Wave 5 |
| 预览 | `R01-07` | 部分完成 | 部分完成 | 部分完成 | 未开始 | 部分完成 | Wave 1 |
| 生产部署 | `R20-11`, `R23-08`, `R23-09`, `R23-10`, `R23-11`, `R23-12` | 部分完成 | 未开始 | 未开始 | 未开始 | 未开始 | Wave 0 |
| 答卷与摘要 | `R06-01`, `R19-02` | 完成 | 未开始 | 未开始 | 未开始 | 部分完成 | Wave 3 |
| 模板与品牌 | `R01-03`, `R01-06`, `R19-05`, `R19-06`, `R19-10` | 部分完成 | 未开始 | 未开始 | 未开始 | 部分完成 | Wave 2 |
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
- **后端** （实现）[`platform/services/business/src/main/java/cn/mjy/platform/delivery/DeliveryTaskController.java`](../../services/business/src/main/java/cn/mjy/platform/delivery/DeliveryTaskController.java) 定位 `public class DeliveryTaskController`：批量任务、回执、退订和催答后端已形成；真实供应商未接入。
- **前端** （审计）[`docs/audits/2026-10-09-product-alignment/14-full-development-report.md`](../../../docs/audits/2026-10-09-product-alignment/14-full-development-report.md) 定位 `| 通讯录、部门、标签、名单 |`：核查报告明确记录通讯录和投放无前端入口。
- **需求** （需求规格）[`platform/docs/traceability/requirement-index.md`](../traceability/requirement-index.md) 定位 `| R18-01 |`：通讯录导入与同步需求已编号。

### 投放链接与二维码 (`delivery-links`)

- **后端** （实现）[`platform/services/business/src/main/java/cn/mjy/platform/delivery/DeliveryLinkController.java`](../../services/business/src/main/java/cn/mjy/platform/delivery/DeliveryLinkController.java) 定位 `public class DeliveryLinkController`：投放链接、短链和二维码后端接口已存在。
- **前端** （审计）[`docs/audits/2026-10-09-product-alignment/14-full-development-report.md`](../../../docs/audits/2026-10-09-product-alignment/14-full-development-report.md) 定位 `| 投放链接、二维码、短链 |`：核查报告明确记录该能力无管理端页面。
- **需求** （需求规格）[`platform/docs/traceability/requirement-index.md`](../traceability/requirement-index.md) 定位 `| R05-01 |`：链接、二维码和短链需求已编号。
- **追溯** （追溯记录）[`platform/docs/traceability/requirement-tests.md`](../traceability/requirement-tests.md) 定位 `| R05-01 | 单测 |`：链接签名和短链测试证据已登记。

### 编辑 (`editing`)

- **后端** （实现）[`platform/services/business/src/main/java/cn/mjy/platform/survey/SurveyController.java`](../../services/business/src/main/java/cn/mjy/platform/survey/SurveyController.java) 定位 `public class SurveyController`：草稿读取、保存和版本控制接口已存在。
- **流程** （审计）[`docs/audits/2026-10-09-product-alignment/14-full-development-report.md`](../../../docs/audits/2026-10-09-product-alignment/14-full-development-report.md) 定位 `#### Step 3：问卷编辑`：核查报告确认基础作者链路可用，同时记录 Phase C 缺口。
- **前端** （实现）[`platform/apps/admin-web/src/features/editor/EditorPage.tsx`](../../apps/admin-web/src/features/editor/EditorPage.tsx) 定位 `export function EditorRoutePage`：基础题型编辑、排序和保存已接入；高级题型仍只读。
- **需求** （需求规格）[`platform/docs/traceability/requirement-index.md`](../traceability/requirement-index.md) 定位 `| R01-04 |`：基础编辑需求已编号。

### 导出 (`exports`)

- **后端** （实现）[`platform/services/business/src/main/java/cn/mjy/platform/response/ResponseExportController.java`](../../services/business/src/main/java/cn/mjy/platform/response/ResponseExportController.java) 定位 `public class ResponseExportController`：导出作业创建、进度、下载和再次授权接口已存在。
- **前端** （审计）[`docs/audits/2026-10-09-product-alignment/14-full-development-report.md`](../../../docs/audits/2026-10-09-product-alignment/14-full-development-report.md) 定位 `| 导出任务和下载 |`：核查报告记录导出无前端入口，且 PDF 等子项未完成。
- **需求** （需求规格）[`platform/docs/traceability/requirement-index.md`](../traceability/requirement-index.md) 定位 `| R06-02 |`：Excel 和 CSV 导出需求已编号。
- **追溯** （追溯记录）[`platform/docs/traceability/requirement-tests.md`](../traceability/requirement-tests.md) 定位 `| R06-03 |`：SAV 导出的局部自动化证据已登记。

### 导入 (`import`)

- **后端** （实现）[`platform/services/business/src/main/java/cn/mjy/platform/survey/SurveyController.java`](../../services/business/src/main/java/cn/mjy/platform/survey/SurveyController.java) 定位 `@PostMapping("/{id}/import/preview")`：导入预览与提交接口已存在。
- **前端** （实现）[`platform/apps/admin-web/src/features/import/ImportPage.tsx`](../../apps/admin-web/src/features/import/ImportPage.tsx) 定位 `export function ImportRoutePage`：导入页面支持预览、异常行和选择性导入。
- **需求** （需求规格）[`platform/docs/traceability/requirement-index.md`](../traceability/requirement-index.md) 定位 `| R01-02 |`：批量文本导入需求已编号。
- **追溯** （追溯记录）[`platform/docs/traceability/requirement-tests.md`](../traceability/requirement-tests.md) 定位 `| R01-02 | 单测 |`：后端导入行为已有登记证据。

### 预览 (`preview`)

- **流程** （审计）[`docs/audits/2026-10-09-product-alignment/14-full-development-report.md`](../../../docs/audits/2026-10-09-product-alignment/14-full-development-report.md) 定位 `#### Step 5：快速预览`：核查报告明确记录真实运行时预览尚未交付。
- **前端** （实现）[`platform/apps/admin-web/src/features/preview/PreviewPage.tsx`](../../apps/admin-web/src/features/preview/PreviewPage.tsx) 定位 `export function PreviewRoutePage`：当前页面仅提供不提交答卷的本地草稿快速预览。
- **需求** （需求规格）[`platform/docs/traceability/requirement-index.md`](../traceability/requirement-index.md) 定位 `| R01-07 |`：预览、发布与关闭为复合需求。

### 生产部署 (`production-deployment`)

- **后端** （实现）[`platform/deploy/private/docker-compose.private.yml`](../../deploy/private/docker-compose.private.yml) 定位 `services:`：现有私有开发部署提供基础，但不是 Production Compose。
- **生产** （审计）[`platform/README.md`](../../README.md) 定位 `当前仍不能宣称正式 production-ready`：仓库状态明确记录生产代码已存在，但真实 Release 和四平台矩阵仍未完成。
- **需求** （需求规格）[`platform/docs/traceability/requirement-index.md`](../traceability/requirement-index.md) 定位 `| R20-11 |`：本地私有化部署需求已编号。
- **追溯** （追溯记录）[`docs/audits/2026-10-09-product-alignment/14-full-development-report.md`](../../../docs/audits/2026-10-09-product-alignment/14-full-development-report.md) 定位 `生产部署路径、TLS、mTLS`：核查报告记录生产部署和供应链门禁尚未闭环。

### 答卷与摘要 (`responses-summary`)

- **后端** （实现）[`platform/services/business/src/main/java/cn/mjy/platform/response/ResponseQueryController.java`](../../services/business/src/main/java/cn/mjy/platform/response/ResponseQueryController.java) 定位 `public class ResponseQueryController`：跨版本答卷查询、摘要和字段接口已存在。
- **后端** （自动化测试）[`platform/services/business/src/test/java/cn/mjy/platform/response/ResponseTenantIsolationTest.java`](../../services/business/src/test/java/cn/mjy/platform/response/ResponseTenantIsolationTest.java) 定位 `class ResponseTenantIsolationTest`：答卷租户隔离已有后端测试。
- **前端** （审计）[`docs/audits/2026-10-09-product-alignment/14-full-development-report.md`](../../../docs/audits/2026-10-09-product-alignment/14-full-development-report.md) 定位 `| 答卷列表、摘要、字段 |`：核查报告明确记录答卷列表、摘要和字段无页面。
- **需求** （需求规格）[`platform/docs/traceability/requirement-index.md`](../traceability/requirement-index.md) 定位 `| R06-01 |`：答卷查询需求已编号。

### 模板与品牌 (`templates-branding`)

- **后端** （实现）[`platform/contracts/survey-branding-v1.md`](../../contracts/survey-branding-v1.md) 定位 `# 问卷品牌与多语言 v1`：品牌与多语言已定义契约。
- **后端** （实现）[`platform/services/business/src/main/java/cn/mjy/platform/survey/template/SurveyTemplateController.java`](../../services/business/src/main/java/cn/mjy/platform/survey/template/SurveyTemplateController.java) 定位 `class SurveyTemplateController`：租户模板生命周期后端接口已存在。
- **前端** （审计）[`docs/audits/2026-10-09-product-alignment/14-full-development-report.md`](../../../docs/audits/2026-10-09-product-alignment/14-full-development-report.md) 定位 `| 模板库与平台模板 |`：核查报告明确记录模板和品牌均无配置入口。
- **需求** （需求规格）[`platform/docs/traceability/requirement-index.md`](../traceability/requirement-index.md) 定位 `| R19-05 |`：企业模板库需求已编号。

### 工作台 (`workspace`)

- **后端** （实现）[`platform/services/business/src/main/java/cn/mjy/platform/access/ResourceTreeController.java`](../../services/business/src/main/java/cn/mjy/platform/access/ResourceTreeController.java) 定位 `public class ResourceTreeController`：资源树后端接口已存在。
- **流程** （审计）[`docs/audits/2026-10-09-product-alignment/14-full-development-report.md`](../../../docs/audits/2026-10-09-product-alignment/14-full-development-report.md) 定位 `#### Step 1：资源工作台`：页面核查确认工作台可操作，同时记录缺少总览和待办。
- **前端** （实现）[`platform/apps/admin-web/src/features/workspace/WorkspacePage.tsx`](../../apps/admin-web/src/features/workspace/WorkspacePage.tsx) 定位 `export function WorkspacePage`：工作台页面已接入真实资源操作。
- **需求** （需求规格）[`platform/docs/traceability/requirement-index.md`](../traceability/requirement-index.md) 定位 `| R01-01 |`：空白与应用类型创建需求已编号。
