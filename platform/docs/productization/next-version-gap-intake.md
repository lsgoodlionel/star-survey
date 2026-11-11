# 下一版本差距盘点入口（草案）

日期：2026-11-11  
基线：`main` `825e7795c956c453c2cb99605a9e66367f7ed2f1`  
状态：**待产品范围确认；不改变 Wave 0–6 的冻结范围和机器能力状态**

## 目的与证据边界

本页承接[下一版本范围 Issue #106](https://github.com/lsgoodlionel/star-survey/issues/106)，只给出开始原子化核查的目录。现行[蓝图](product-blueprint.md)将旧阶段限定在已有后端产品化和单机交付，明确排除新统计、AI、支付、预约、签署、投票、360 与体验包。[路线图](release-roadmap.md)和[机器能力映射](capabilities.json)继续是旧阶段进度和状态真源；截至本基线，12 项中 11 项为 `partial`，`production-deployment` 为 `not_started`。代码、路由、CI 或候选成功均不能直接提升为六层 `accepted`。

W01–W20 来自 2026-10-27 问卷星公开资料整理；其 Survey 对照使用 2026-11-06 旧基线。公开目录不证明各版本或登录后实际行为。开始实现前须核对当日[使用手册](https://www.wjx.cn/app/themehtml/wjxguide.aspx)、[版本矩阵](https://www.wjx.cn/app/themehtml/plan.aspx)与必要的真实账号旅程。官方资料中的“50 多种”和“90+”题型口径不同，不能按数量推断可用清单。

下表的“现有证据”只表示已定位的交付或范围边界，**不是 W 域完成率**。“未知”表示尚未按原子功能逐项查证，不能改写为“未实现”或“已实现”。

| 域 | 对标目录 | Survey 现有证据与边界 | 下一步 |
| --- | --- | --- | --- |
| W01 | 产品类型 | 原蓝图仅产品化调查主链；考试、360、投票、支付等未纳入旧范围 | 逐产品线确定目标与独立契约 |
| W02 | 创建与内容 | 编辑、导入、模板、品牌有旧 Wave 交付；题库、批量录题等未逐项核实 | 拆题型/素材/题库/迁移清单 |
| W03 | 题型与逻辑 | Wave 5 首批折叠栏目、多级下拉、轮播图有双库浏览器阶段证据；发布网关已有 v2 条件逻辑 DSL 与校验/编译测试，但编辑器原先没有题目条件配置。草稿 #110 正补前置单选显示条件，其余复杂逻辑未知 | 逐题型验证配置至导出纵向旅程 |
| W04 | 填写治理 | 发布、受众和作答主链有实现；设备限制等公开细项未知 | 对照服务端策略、失败和越权 |
| W05 | 分发入口 | 链接、二维码、名单、批量任务已进入旧 Wave；真实 SMS/其他供应商由 #35 延期 | 核对嵌入、参数、渠道与回执 |
| W06 | 回收交付 | Wave 3 答卷、摘要、多格式导出有源码测试；正式身份不可变候选旅程见 #38 | 按格式、权限与失败恢复验收 |
| W07 | 分析 | 现行蓝图有基础摘要，明确排除新增统计；交叉分析/大屏/多问卷未纳入旧范围 | 先定指标口径、脱敏与数据模型 |
| W08 | 360 与人才 | 原蓝图排除 360 场景包 | 独立产品契约和授权关系 |
| W09 | 满意度与体验 | 原蓝图排除体验管理包 | 定义指标/趋势/预警及数据边界 |
| W10 | 考试 | 原蓝图未纳入考试产品线 | 定义题库、计时、判分、防作弊边界 |
| W11 | 测评 | 原蓝图未纳入测评产品线 | 定义维度、计分和报告契约 |
| W12 | 投票 | 原蓝图排除投票场景包 | 独立投票/防刷/展示契约 |
| W13 | 表单与流程 | 现有审批是问卷发布审批，不等于通用表单流程 | 核对登记、签到、流程流转范围 |
| W14 | 预约与收款 | 原蓝图明确排除预约、支付 | 需真实支付和名额一致性方案 |
| W15 | 在线签署 | 原蓝图明确排除签署 | 需文档/签名/法律及存证产品决策 |
| W16 | 协作与运营 | Wave 6 运营后台与权限分离代码已入主线，生产验收仍见 #32/#34 | 对照文件夹、协作及正式身份旅程 |
| W17 | 品牌与国际化 | Wave 2 模板/品牌/多语言有本地阶段证据；自定义域名及深度主题未知 | 逐项核对实际前台、权限与发布快照 |
| W18 | 集成与部署 | 单机生产骨架和 rc19 四平台候选证据见 #33；正式 Release/生产见 #34 | 拆 API/SSO/组织集成与部署产品线 |
| W19 | AI | 原蓝图明确排除 AI | 定义模型、数据授权、成本和评测后另立计划 |
| W20 | 服务型业务 | 商业服务目录，不是软件功能 | 单列服务运营，不作菜单验收 |

## 核心旅程逐项核查（截至 2026-11-11）

此处“阶段已验”仅指现有本地或 CI 切片证据；所有项目的生产层仍按机器映射和对应旧阶段 Issue 判定。代码路径为可复核定位，不代表每个公开细项等价。

| W 域与用户动作 | 当前判断 | 可复核证据 | 下一步准出 |
| --- | --- | --- | --- |
| W02 空白创建、资源与文件夹 | 已实现且有阶段验证 | [创建 API](../../apps/admin-web/src/shared/api/surveys.ts)、[工作台浏览器旅程](../../apps/admin-web/e2e/dashboard.spec.ts) | 正式身份候选复核资源权限 |
| W02 模板、批量导入 | 已实现且有阶段验证 | [模板品牌浏览器旅程](../../apps/admin-web/e2e/template-branding.spec.ts)、[导入页](../../apps/admin-web/src/features/import/ImportPage.tsx) | 逐格式/失败行/版本候选复核 |
| W02 题库、问卷转移、企业素材库 | 未知 | 公开目录已有；本轮未定位完整自有管理端旅程 | 拆原子任务并核对 API、界面和权限 |
| W03 基础题与首批高级题 | 已实现且有阶段验证 | [编辑器](../../apps/admin-web/src/features/editor/EditorPage.tsx)提供 X/L/M/S/T 与三种主题；[Wave 5 双库旅程](../../apps/admin-web/e2e/authoring.spec.ts) | 其他题型逐项核对，不能按题型总数推断 |
| W03 前置单选显示条件 | 已实现但未验收（草稿） | [网关 DSL 契约](../../contracts/survey-logic-dsl-v1.md)已有；[PR #110](https://github.com/lsgoodlionel/star-survey/pull/110)新增编辑器与浏览器测试，CI/复审待完成 | 精确 head 双库浏览器、独立复审、合并后再判阶段交付 |
| W03 复杂条件、校验、计分 | 后端有契约；管理端旅程未验证 | [DSL v2 契约](../../contracts/survey-logic-dsl-v1.md)、[网关测试](../../tools/publish-gateway/tests/test_logic_publish.py)；当前编辑器无相应完整配置路径 | 逐项做安全可视配置、发布拒绝与答卷回读 |
| W04 时间窗、验证码、邀请码、限制及网络规则 | 服务端已有 `policy` v1 保存、编译和插件执行；尚无管理端策略配置旅程 | [访问策略契约](../../contracts/survey-access-policy-v1.md)、[双库 HTTP 端到端驱动](../../deploy/test/run-access-policy.sh)覆盖策略执行；[正式链接面板](../../apps/admin-web/src/features/publish/PublishedAccessPanel.tsx)目前只创建/撤销链接，未暴露策略字段 | 先按来源逐项确认策略范围与权限；管理端从单一时间窗起切片，验证草稿保存→发布→真实作答允许/拒绝、时区边界与越权，双库浏览器准出。编辑器文件与 #110 重叠，待其审查收口后再动实现 |
| W05 链接、二维码、名单投放 | 已实现且有阶段验证 | [投放工作区](../../apps/admin-web/src/features/delivery/DeliveryWorkspacePage.tsx)、[主作者浏览器旅程](../../apps/admin-web/e2e/authoring.spec.ts)、[#40](https://github.com/lsgoodlionel/star-survey/issues/40) | 正式运营身份不可变候选验收 |
| W05 SMS 与其他邮件真实回执 | 已批准延期 | [#35](https://github.com/lsgoodlionel/star-survey/issues/35) | 用户提供供应商条件后独立联调，不以模拟结果关闭 |
| W05 嵌入、小程序、更多外部渠道 | 未知 | 公开目录有细项，本轮未定位完整纵向旅程 | 先确认版本及入口，再核对安全边界 |
| W06 跨版本答卷与多格式导出 | 已实现且有阶段验证；候选未完成 | [答卷查询](../../apps/admin-web/src/features/responses/ResponsesPage.tsx)、[导出旅程](../../apps/admin-web/e2e/template-branding.spec.ts)、[#38](https://github.com/lsgoodlionel/star-survey/issues/38) | 正式身份不可变候选逐格式语义、权限和故障恢复 |
| W07 基础摘要 | 已实现且有阶段验证 | [ResponseQueryController](../../services/business/src/main/java/cn/mjy/platform/response/ResponseQueryController.java)、[答卷摘要浏览器旅程](../../apps/admin-web/e2e/authoring.spec.ts) | 正式数据身份/脱敏候选验证 |
| W07 交叉分析、大屏、多问卷及 AI 报告 | 未纳入旧蓝图；实现范围未定 | [旧蓝图冻结范围](product-blueprint.md)明确不新增统计与 AI | 定指标、数据模型、权限和报告契约后立项 |
| W17 模板、品牌、多语言与素材固定 | 已实现且有本地阶段验收 | [品牌契约](../../contracts/survey-branding-v1.md)、[双库浏览器旅程](../../apps/admin-web/e2e/template-branding.spec.ts)、[#36](https://github.com/lsgoodlionel/star-survey/issues/36) | 深度主题和自定义域名单列核查 |
| W17 自定义域名、CSS/JS 扩展 | 未知 | 公开目录有细项，旧 Wave 2 阶段验收未声称覆盖 | 先做安全/租户边界与产品取舍 |

## 执行顺序与准出

1. **范围决策**：用户已同意先核查并推进核心调查旅程 W02–W07、W17；W01–W20 是否为长期目标、哪些场景纳入下一版，仍待确定。旧阶段遗留按对应 Issue 跟踪；#41 已有 rc19 人工 VoiceOver 初步通过记录并关闭，不再列为当前待做。
2. **原子矩阵**：每项记录来源 URL/适用版本、Survey 用户任务、引擎/平台/前端代码定位、真实浏览器旅程、权限/失败/移动/无障碍、生产证据，以及“已实现且验收、已实现未验收、未实现、未知、批准延期”之一。复用仓库现有需求追溯，不新建第二套总状态。
3. **短切片实现**：优先选择不与现行工作树冲突的核心链缺口；写失败测试，再补三端契约和实现。数据库旅程按既有约定做 MySQL/PostgreSQL 真实浏览器验证；精确 head CI 与独立审查后才判断阶段状态。
4. **门禁分离**：PR/CI 绿只证明对应代码切片；正式身份不可变候选、人工读屏（#41 的 rc19 记录已通过）、原生生命周期、供应商和生产各保留独立证据。Release 工作流由 Claude Code 负责，本草案不修改或触发。

首个 W03 前置单选显示条件切片见 [draft PR #110](https://github.com/lsgoodlionel/star-survey/pull/110)，仍待 CI 和复审，不算交付完成。下一步勾选和责任归属见 [Issue #106](https://github.com/lsgoodlionel/star-survey/issues/106)。本页不更新 `capabilities.json`、旧蓝图或 Release 配置。
