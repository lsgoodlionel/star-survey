# 自治执行进度

计划：`docs/superpowers/plans/2026-10-11-template-branding-wave2.md`

分支：`feat/admin-product-alignment`

## 当前状态

Task 1: complete - Wave 2 已激活；治理完成记录见 SDD 账本。
Task 2: complete - 品牌资产跨 Java/Python/PHP 边界已实现并通过 2026-10-27 双库真实 E2E、PHPUnit、Gateway、Java 定向和 production 配置门禁；发布与预览的错误 channel key 回滚均验证问卷删除、上传目录清除及无作者品牌残留。能力矩阵仍按 Wave 2 完整六层证据维持原状态。
Task 3: complete - 模板、资产与品牌 typed client 已接入现有端点；FormData 上传、Zod 响应校验、租户缓存键及品牌草稿安全合并已实现。2026-10-27 定向 18 项与管理端全套 317 项通过，lint、typecheck、build、production bundle 检查通过；页面流程仍由 Task 4-7 承接。
Task 4: complete - 模板中心列表、范围与状态 URL 筛选、版本/历史详情、有效项目或文件夹目标选择及独立问卷复制已接入；工作区与全局导航可进入。2026-10-27 定向 29 项、管理端全套 325 项通过，lint、typecheck、build、production bundle 检查通过；真实浏览器与双库纵向验收留待 Task 7。
Task 5: complete - 用户确认后补入最小只读管理列表与模板动作能力接口，沿用同租户 RLS 和真实权限判定；前端已接入创建/添加版本、送审、批准、驳回、下架、运营 JSON 导入与选版上架。2026-10-27 Java 全量 1492 项/183 类（失败、错误、跳过均为 0），前端全套 345 项、lint、typecheck、build、production bundle 检查通过。Task 6–7 的品牌编辑器及真实浏览器纵向验收尚未完成，Wave 2 能力状态不提升。
Task 6: complete - 品牌与语言编辑页、受控图片选择/上传、题组/题目/子题/选项结构化译文、草稿乐观锁保存、未保存离开拦截及 dashboard 最近访问已接入；V934 仅扩展品牌页受控枚举并保留版本约束。2026-10-27 隔离数据库 Java 全量 1492 项/183 类（失败、错误、跳过均为 0），前端全套 352 项、lint、typecheck、build、production bundle 检查通过。Task 7 真实浏览器纵向验收及 Task 8 生产门禁仍待完成，Wave 2 能力状态不提升。
Task 7: complete - 2026-10-27 隔离 MySQL 与 PostgreSQL 真实浏览器均为 19/19，通过模板生命周期、平台模板复制、品牌/语言保存、409 冲突、带图预览、正式答卷、两版发布、旧版图片字节固定、无第三方请求及四断点布局；平台 API、平台 DB、网关与引擎 DB 交叉核验通过。预览 502 的最终断点是冷启动引擎未注册 zh-business，已复用既有主题安装器补齐；同时修复资产 JSON 枚举契约、受控环回 HTTP 物化地址、插件通道测试配置及品牌保存后发布概览缓存。前端 353 项、网关 1235 项、门禁单元 48 项、隔离 Java 1494 项/184 类通过（失败、错误、跳过均为 0），lint、typecheck、build、production bundle 检查通过。实现分界提交：1ed42c34a2、9b059a7523、c80e9b406b；最终实现提交为 c80e9b406b，Task 8 完成前不提升 Wave 2 整体能力状态。
Task 8: pending - 用户已明确本轮完成本地验收与同步，生产 Compose/Caddy、doctor、备份恢复保留待办。已授权的两项脚本执行位变更仅以模式提交 e00a7b5a32 同步，治理映射提交 db267ede9d 已推送；该提交的 GitHub 平台质量门 run 40461852500 全部通过（10/10 jobs，含真实浏览器）。治理 run 40461850858 的映射失败已消除，仍有 strict_unittest 共享 defaultTestLoader 导致临时目录不可导入的两项 ERROR；核心归独立控制平面仓库维护，本轮未修改。Harness 最终门禁、三路独立复审与六层交付仍未完成，Wave 2 整体能力状态不提升。

2026-10-27 Task 8 接续核验（实现 HEAD `28a5e18925`，文档提交另计）：隔离 Harness run `0416bb7c-235d-4107-b050-6a05e45a304a` 的 Java、Gateway、管理端测试/静态检查/构建/production bundle 及文档门共 11 项通过；新增浏览器门在独立 worktree 的 Docker Desktop 嵌套挂载阶段失败，未执行浏览器用例，Harness 仍处于 repairing。原 worktree 直接复验 Java 1494 项/184 类、管理端 353 项、Gateway 1235 项通过；生产部署 Python 测试 105 项中 104 项通过，真实原生验收按前置条件跳过，Python 编译通过。MySQL 浏览器首次在 `authoring.spec.ts:594` 的“新建”按钮键盘焦点断言失败，隔离重跑 19/19；PostgreSQL 随后在同一断言失败（6 项通过、1 项失败、12 项未运行），依“两次同类失败即停”约定停止自动修复，保留 `/tmp/survey-wave2-browser-{mysql-final,mysql-retry,pgsql-final}.log` 脱敏诊断。GitHub 当前无 `v*` tag/Release；自动生成 `release.json` 的 `release.yml` 仅在开发分支，默认分支未注册，首版 baseline policy 为 `0.3.0-rc.1` bootstrap，第二版需真实前版 manifest SHA-256；用户确认尚未制作两版 Release。指定验收服务器的只读 SSH 连续两次连接超时；生产 Compose/Caddy、doctor、备份恢复和三路独立复审均无通过证据。Wave 2 未满足准出，Wave 3 新任务窗口尚未创建。

2026-10-12：用户要求生成报告准备切换模型，暂停新增实现。接续入口为
[模型切换交接报告](run-history/2026-10-12-wave2-model-handoff.md)。
本轮网关 1,232 项和既有品牌双语 MySQL E2E 通过；不代表新增 brandAssets 真实链路通过。
这段 2026-10-12 历史记录当时没有完整交付证据；Task 2 的后续验收见下方当前状态和[接续记录](run-history/2026-10-12-wave2-model-handoff.md)。Task 2 完成不提升 Wave 2 整体 capability 或 Harness completed 状态。

Dashboard Task 1: complete - 治理 HEAD 绑定、hosted agent-governance run 19 与控制平面迁移已完成。

Wave 2: active - Dashboard Task 1 治理闭环已完成，Wave 2 计划已激活。

## Token 控制平面（2026-10-12）

Task 1-10: delivered - Context Capsule、Gate Summary/证据复用、并发租约、Token Telemetry、
GitHub 受信摘要、全局规则安装与 Obsidian 单向投影均已实现，并通过独立复审。完整 Harness 套件 490 项通过，8 项仅因
当前 macOS 无法执行 Linux 权限夹具而跳过；12 个故障演练全部通过。真实 Survey Capsule 为 7,538 bytes，
相对 204,800-byte 基线减少 96.32%，启动 active-token 估算减少 96.23%。

实现与验收提交 `d586196354` 已推送到 `origin/feat/admin-product-alignment`；Obsidian 的项目总览与进度路线图
两个既有 marker 块已同步且无 pending。两个预先暂存的 Wave 2 测试脚本执行位及全部业务改动均未纳入该提交。

本次优化不提升 Wave 2 业务能力状态。后续仍从 Task 2 验收断点继续：先运行 PHPUnit，再完成品牌图片
发布/预览的 MySQL 与 PostgreSQL 真实双库验证；只有这两项通过后才进入 Task 3。35%-45% active Token
降幅仍是预测，需在后续两个产品 Milestone 结束后用项目归属明确的遥测复核。

最新后期进度、Wave 0-6 成熟度、剩余工作量和 Token 速度见
[2026-10-12 Survey 后期开发与整体进度分析](../audits/2026-10-12-survey-late-stage-progress-token-analysis.md)。
该报告维持产品口径 `accepted=0/12`，不把当前未提交的 Wave 2 Task 2 代码计为交付；短窗口 Token
下降仅作为趋势证据，不替代两个代表性产品 Milestone 后的复测。

## Dashboard 历史证据（非本批 Wave 2 验收）

- Dashboard 实现提交：`3ec78f09706c20b1a2ead20e19ee3f42993ab87e`。
- Harness 实现与 hosted fixture 修复提交：`b40a41099f9d5c4824ec8e34b873a32def46733b`。
- 本地 Harness run：`6c118d24-f546-4cbc-9277-edb5db52015b`，16/16 gates 通过。
- Admin Web：30 个测试文件、299 项测试通过；lint、typecheck、build 和 production bundle 校验通过。
- 平台：1478 项测试、182 个测试类，零 failure、error、skip。
- 真实浏览器：15/15 场景通过，覆盖 819/820/1179/1180 边界。
- Harness 非 Linux 单元测试：411/411 通过；Git guard：53/53 通过。

## 遗留边界

- GitHub Actions run 19 已验证 fresh checkout 的可追踪账本契约；Wave 2 业务实现与后续六层交付证据仍待完成。
- 仓库证明要求来自 `main` 的 GitHub artifact attestation；功能分支不得伪造完成状态。
- Wave 2、4、5、6、生产 Release、公开 TLS 与服务器部署仍按父计划后续推进。

## 更新规则

本文只记录脱敏摘要、当前 HEAD 的 Gate 结果、完成项、遗留问题和下一步。详细 SDD 执行日志保存在忽略目录，不进入仓库；秘密、原始模型事件和完整运行日志不得写入本文。
