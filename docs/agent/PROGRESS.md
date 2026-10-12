# 自治执行进度

计划：`docs/superpowers/plans/2026-10-11-template-branding-wave2.md`

分支：`feat/admin-product-alignment`

## 当前状态

Task 1: complete - Wave 2 已激活；治理完成记录见 SDD 账本。
Task 2: in_progress - 品牌资产跨端实现已在工作区；真实新资源发布/预览与 PHPUnit 验收未闭环。
Task 3: pending - 模板、资产与品牌 client 待实施。
Task 4: pending - 模板浏览与复制待实施。
Task 5: pending - 模板生命周期待实施。
Task 6: pending - 品牌与语言编辑器待实施。
Task 7: pending - 模板到品牌化作答旅程待实施。
Task 8: pending - 生产门禁与交付同步待实施。

2026-10-12：用户要求生成报告准备切换模型，暂停新增实现。接续入口为
[模型切换交接报告](run-history/2026-10-12-wave2-model-handoff.md)。
本轮网关 1,232 项和既有品牌双语 MySQL E2E 通过；不代表新增 brandAssets 真实链路通过。
当前未提交代码没有绑定完整交付证据，不提升 capability 或 Harness completed 状态。

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
