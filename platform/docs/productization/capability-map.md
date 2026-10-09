# 产品化能力基线

> 本文件由 `platform/tools/productization/render_capabilities.py` 根据 `capabilities.json` 生成，请勿手工编辑。

产品完成必须具备需求、后端、前端、流程、生产和追溯六层证据。后端完成不等于产品验收通过。

## 汇总

- 能力项：12
- 产品验收通过：0
- 工程切片完成：3
- 进行中：8
- 已规划：1

## 能力矩阵

| 能力 | 需求编号 | 后端 | 前端 | 流程 | 生产 | 总体状态 | 下一波次 |
|---|---|---|---|---|---|---|---|
| 管理 | `R19-01`, `R19-03`, `R20-01`, `R20-02`, `R20-03`, `R22-01`, `R22-11`, `R22-12` | 部分完成 | 未开始 | 未开始 | 未开始 | 进行中 | Wave 6 |
| 审批与发布 | `R01-07`, `R01-09` | 完成 | 完成 | 完成 | 未开始 | 工程切片完成 | Wave 1 |
| 通讯录与投放 | `R05-04`, `R05-05`, `R05-06`, `R18-01`, `R18-02`, `R18-03`, `R18-06` | 部分完成 | 未开始 | 未开始 | 未开始 | 进行中 | Wave 4 |
| 投放链接与二维码 | `R05-01`, `R05-07`, `R05-08` | 完成 | 未开始 | 未开始 | 未开始 | 进行中 | Wave 1 |
| 编辑 | `R01-04`, `R01-05`, `R01-08` | 完成 | 部分完成 | 部分完成 | 未开始 | 进行中 | Wave 5 |
| 导出 | `R06-02`, `R06-03`, `R06-05`, `R06-06`, `R06-07` | 部分完成 | 未开始 | 未开始 | 未开始 | 进行中 | Wave 3 |
| 导入 | `R01-02` | 完成 | 完成 | 完成 | 未开始 | 工程切片完成 | Wave 5 |
| 预览 | `R01-07` | 部分完成 | 部分完成 | 部分完成 | 未开始 | 进行中 | Wave 1 |
| 生产部署 | `R20-11`, `R23-08`, `R23-09`, `R23-10`, `R23-11`, `R23-12` | 部分完成 | 未开始 | 未开始 | 未开始 | 已规划 | Wave 0 |
| 答卷与摘要 | `R06-01`, `R19-02` | 完成 | 未开始 | 未开始 | 未开始 | 进行中 | Wave 3 |
| 模板与品牌 | `R01-03`, `R01-06`, `R19-05`, `R19-06`, `R19-10` | 部分完成 | 未开始 | 未开始 | 未开始 | 进行中 | Wave 2 |
| 工作台 | `R01-01`, `R19-04` | 完成 | 完成 | 完成 | 未开始 | 工程切片完成 | Wave 6 |

## 证据

### 管理 (`administration`)

- **后端** [`platform/services/business/src/main/java/cn/mjy/platform/tenant/TenantAdminController.java`](../../services/business/src/main/java/cn/mjy/platform/tenant/TenantAdminController.java)：租户生命周期管理后端接口已存在。
- **后端** [`platform/services/business/src/main/java/cn/mjy/platform/tenant/engine/EngineInstanceAdminController.java`](../../services/business/src/main/java/cn/mjy/platform/tenant/engine/EngineInstanceAdminController.java)：引擎实例运营接口已存在。
- **前端** [`docs/audits/2026-10-09-product-alignment/14-full-development-report.md`](../../../docs/audits/2026-10-09-product-alignment/14-full-development-report.md)：核查报告记录租户、套餐、组织连接和引擎实例均无运营页面。
- **需求** [`platform/docs/traceability/requirement-index.md`](../traceability/requirement-index.md)：团队、组织连接、套餐和租户生命周期需求已编号。

### 审批与发布 (`approval-publish`)

- **后端** [`platform/services/business/src/main/java/cn/mjy/platform/survey/PublishApprovalController.java`](../../services/business/src/main/java/cn/mjy/platform/survey/PublishApprovalController.java)：审批状态机与发布接口已存在。
- **流程** [`platform/apps/admin-web/e2e/authoring.spec.ts`](../../apps/admin-web/e2e/authoring.spec.ts)：真实浏览器链路覆盖提交、批准、发布和版本查看。
- **前端** [`platform/apps/admin-web/src/features/publish/PublishPage.tsx`](../../apps/admin-web/src/features/publish/PublishPage.tsx)：审批、发布状态和版本入口已接入。
- **需求** [`platform/docs/traceability/requirement-index.md`](../traceability/requirement-index.md)：发布与审核需求已编号。
- **追溯** [`platform/docs/traceability/requirement-tests.md`](../traceability/requirement-tests.md)：R01-09 的审批绑定版本和绕过防护证据已登记。

### 通讯录与投放 (`contacts-delivery`)

- **后端** [`platform/services/business/src/main/java/cn/mjy/platform/contacts/ContactController.java`](../../services/business/src/main/java/cn/mjy/platform/contacts/ContactController.java)：联系人、部门、标签、名单和参与登记后端已形成。
- **后端** [`platform/services/business/src/main/java/cn/mjy/platform/delivery/DeliveryTaskController.java`](../../services/business/src/main/java/cn/mjy/platform/delivery/DeliveryTaskController.java)：批量任务、回执、退订和催答后端已形成；真实供应商未接入。
- **前端** [`docs/audits/2026-10-09-product-alignment/14-full-development-report.md`](../../../docs/audits/2026-10-09-product-alignment/14-full-development-report.md)：核查报告明确记录通讯录和投放无前端入口。
- **需求** [`platform/docs/traceability/requirement-index.md`](../traceability/requirement-index.md)：通讯录、受众和消息投放需求已编号。
- **追溯** [`platform/docs/traceability/requirement-tests.md`](../traceability/requirement-tests.md)：通讯录隔离、导入去重、发送恢复和催答证据已登记。

### 投放链接与二维码 (`delivery-links`)

- **后端** [`platform/services/business/src/main/java/cn/mjy/platform/delivery/DeliveryLinkController.java`](../../services/business/src/main/java/cn/mjy/platform/delivery/DeliveryLinkController.java)：投放链接、短链和二维码后端接口已存在。
- **前端** [`docs/audits/2026-10-09-product-alignment/14-full-development-report.md`](../../../docs/audits/2026-10-09-product-alignment/14-full-development-report.md)：核查报告明确记录该能力无管理端路由和导航。
- **需求** [`platform/docs/traceability/requirement-index.md`](../traceability/requirement-index.md)：链接、二维码、短链、嵌入和渠道参数需求已编号。
- **追溯** [`platform/docs/traceability/requirement-tests.md`](../traceability/requirement-tests.md)：链接签名、短链和失效隐私测试证据已登记。

### 编辑 (`editing`)

- **后端** [`platform/services/business/src/main/java/cn/mjy/platform/survey/SurveyController.java`](../../services/business/src/main/java/cn/mjy/platform/survey/SurveyController.java)：草稿读取、保存和版本控制接口已存在。
- **流程** [`docs/audits/2026-10-09-product-alignment/14-full-development-report.md`](../../../docs/audits/2026-10-09-product-alignment/14-full-development-report.md)：核查报告确认基础作者链路可用，同时记录 Phase C 和高级配置缺口。
- **前端** [`platform/apps/admin-web/src/features/editor/EditorPage.tsx`](../../apps/admin-web/src/features/editor/EditorPage.tsx)：基础题型编辑、排序和保存已接入；高级题型仍只读。
- **需求** [`platform/docs/traceability/requirement-index.md`](../traceability/requirement-index.md)：基础编辑、分页说明和草稿协作需求已编号。

### 导出 (`exports`)

- **后端** [`platform/services/business/src/main/java/cn/mjy/platform/response/ResponseExportController.java`](../../services/business/src/main/java/cn/mjy/platform/response/ResponseExportController.java)：导出作业创建、进度、下载和再次授权接口已存在。
- **前端** [`docs/audits/2026-10-09-product-alignment/14-full-development-report.md`](../../../docs/audits/2026-10-09-product-alignment/14-full-development-report.md)：核查报告记录导出无前端入口，且 PDF 等子项未完成。
- **需求** [`platform/docs/traceability/requirement-index.md`](../traceability/requirement-index.md)：CSV、Excel、SAV、Word、PDF 和附件导出需求已编号。
- **追溯** [`platform/docs/traceability/requirement-tests.md`](../traceability/requirement-tests.md)：SAV、DOCX 和附件作业的局部自动化证据已登记。

### 导入 (`import`)

- **后端** [`platform/services/business/src/main/java/cn/mjy/platform/survey/SurveyController.java`](../../services/business/src/main/java/cn/mjy/platform/survey/SurveyController.java)：导入预览与提交接口已存在。
- **流程** [`platform/apps/admin-web/e2e/authoring.spec.ts`](../../apps/admin-web/e2e/authoring.spec.ts)：作者浏览器链路覆盖批量导入。
- **前端** [`platform/apps/admin-web/src/features/import/ImportPage.tsx`](../../apps/admin-web/src/features/import/ImportPage.tsx)：导入页面支持预览、异常行和选择性导入。
- **需求** [`platform/docs/traceability/requirement-index.md`](../traceability/requirement-index.md)：批量文本导入需求已编号。
- **追溯** [`platform/docs/traceability/requirement-tests.md`](../traceability/requirement-tests.md)：R01-02 的后端和管理端局部证据已登记。

### 预览 (`preview`)

- **流程** [`docs/audits/2026-10-09-product-alignment/12-current-preview-audit.png`](../../../docs/audits/2026-10-09-product-alignment/12-current-preview-audit.png)：页面审计证明快速预览可见，不证明真实 LimeSurvey 运行时。
- **前端** [`platform/apps/admin-web/src/features/preview/PreviewPage.tsx`](../../apps/admin-web/src/features/preview/PreviewPage.tsx)：当前页面仅提供不提交答卷的本地草稿快速预览。
- **需求** [`platform/docs/traceability/requirement-index.md`](../traceability/requirement-index.md)：预览、发布与关闭为复合需求。
- **追溯** [`docs/audits/2026-10-09-product-alignment/14-full-development-report.md`](../../../docs/audits/2026-10-09-product-alignment/14-full-development-report.md)：全量核查明确记录真实运行时预览尚未交付。

### 生产部署 (`production-deployment`)

- **后端** [`platform/deploy/private/docker-compose.private.yml`](../../deploy/private/docker-compose.private.yml)：现有私有开发部署提供基础，但不是本设计要求的 Production Compose。
- **生产** [`platform/README.md`](../../README.md)：仓库状态明确声明尚无生产部署路径，SSL gate 不代表 production-ready。
- **需求** [`platform/docs/traceability/requirement-index.md`](../traceability/requirement-index.md)：私有部署、安全供应链、备份恢复和验收证据需求已编号。
- **追溯** [`docs/audits/2026-10-09-product-alignment/14-full-development-report.md`](../../../docs/audits/2026-10-09-product-alignment/14-full-development-report.md)：核查报告记录生产部署、TLS、签名和漏洞扫描尚未闭环。

### 答卷与摘要 (`responses-summary`)

- **后端** [`platform/services/business/src/main/java/cn/mjy/platform/response/ResponseQueryController.java`](../../services/business/src/main/java/cn/mjy/platform/response/ResponseQueryController.java)：跨版本答卷查询、摘要和字段接口已存在。
- **后端** [`platform/services/business/src/test/java/cn/mjy/platform/response/ResponseTenantIsolationTest.java`](../../services/business/src/test/java/cn/mjy/platform/response/ResponseTenantIsolationTest.java)：答卷租户隔离已有后端测试。
- **前端** [`docs/audits/2026-10-09-product-alignment/14-full-development-report.md`](../../../docs/audits/2026-10-09-product-alignment/14-full-development-report.md)：核查报告明确记录答卷列表、摘要和字段无页面。
- **需求** [`platform/docs/traceability/requirement-index.md`](../traceability/requirement-index.md)：答卷查询和数据分权需求已编号。

### 模板与品牌 (`templates-branding`)

- **后端** [`platform/contracts/survey-branding-v1.md`](../../contracts/survey-branding-v1.md)：品牌与多语言已定义契约和引擎主题能力。
- **后端** [`platform/services/business/src/main/java/cn/mjy/platform/survey/template/SurveyTemplateController.java`](../../services/business/src/main/java/cn/mjy/platform/survey/template/SurveyTemplateController.java)：租户模板生命周期后端接口已存在。
- **前端** [`docs/audits/2026-10-09-product-alignment/14-full-development-report.md`](../../../docs/audits/2026-10-09-product-alignment/14-full-development-report.md)：核查报告明确记录模板和品牌均无配置入口。
- **需求** [`platform/docs/traceability/requirement-index.md`](../traceability/requirement-index.md)：模板、外观、主题与多语言需求已编号。
- **追溯** [`platform/docs/traceability/requirement-tests.md`](../traceability/requirement-tests.md)：企业模板生命周期局部证据已登记。

### 工作台 (`workspace`)

- **后端** [`platform/services/business/src/main/java/cn/mjy/platform/access/ResourceTreeController.java`](../../services/business/src/main/java/cn/mjy/platform/access/ResourceTreeController.java)：资源树后端接口已存在。
- **流程** [`platform/apps/admin-web/e2e/authoring.spec.ts`](../../apps/admin-web/e2e/authoring.spec.ts)：真实浏览器作者链路覆盖工作台创建与进入编辑器。
- **前端** [`platform/apps/admin-web/src/features/workspace/WorkspacePage.tsx`](../../apps/admin-web/src/features/workspace/WorkspacePage.tsx)：工作台页面已接入真实资源操作。
- **需求** [`platform/docs/traceability/requirement-index.md`](../traceability/requirement-index.md)：需求索引登记创建与资源协作需求。
- **追溯** [`platform/docs/traceability/requirement-tests.md`](../traceability/requirement-tests.md)：局部断言和验收边界已登记；不代表整条需求 Accepted。
