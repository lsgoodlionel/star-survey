# Token 高效工程控制平面验收报告

日期：2026-10-12

分支：`feat/admin-product-alignment`

范围：`docs/superpowers/plans/2026-10-12-token-efficient-engineering.md` Task 1-10

## 结论

控制平面已经完成本地实现与集成验收。它减少模型重复读取和日志暴露，但没有跳过、删减或放宽任何测试、
安全、双库、生产或发布门禁。本报告只证明 Harness 与 Token 优化能力，不把工作区中尚未完成验收的 Wave 2
品牌资产代码认定为已交付。

## 验证证据

| 检查 | 结果 | 证据摘要 |
| --- | --- | --- |
| 完整 Harness suite | PASS | `490 tests`，`OK (skipped=8)`，耗时 382.452 秒 |
| 平台特定跳过 | 已界定 | 8 项均为 Linux wrapper 权限夹具；当前执行主机为 macOS |
| 故障演练 | PASS | 12/12：原子写前后中断、事件截断、计划漂移、历史改写、runtime 缺失、秘密脱敏、重复失败、无进展、超时与生成物越界 |
| Governance | PASS | 25/25；Wave 2 Task 1 checkbox、交付 manifest 与 native release-blocking 契约一致 |
| 独立复审 | APPROVED | 首轮 7 项 Important 与 1 项 Minor、后续 native acceptance 契约漂移均已关闭 |
| Doctor | 提交后复核 | 未提交的 Harness wrapper 按安全设计使 Gate runtime 信任检查失败；提交后以 clean HEAD 重新验证 |
| 全局规则安装 | PASS | 首次仅更新三个 marker 管理文件；再次 dry-run 无变化 |

## 定量验收

| 指标 | 限制 | 实测 | 结果 |
| --- | ---: | ---: | --- |
| Context Capsule 总量 | <= 24,576 bytes | 7,538 bytes | PASS |
| 稳定前缀 | <= 12,288 bytes | 7,151 bytes | PASS |
| 单任务增量 | <= 8,192 bytes | 387 bytes | PASS |
| 默认失败诊断 | <= 4,096 bytes | 集成测试断言通过 | PASS |
| 成功 Gate 日志进入摘要 | 0 bytes | 0 bytes | PASS |
| 十个并发相同 Gate | 只运行 1 次 | 1 次执行、9 次复用 | PASS |
| 遥测内容隐私 | 不含 prompt/message/source/log body | fixture 与序列化检查通过 | PASS |
| 原始上下文降低 | >= 80% | 96.32% | PASS |
| 启动 active-token 估算降低 | >= 70% | 96.23% | PASS |

基线 fixture 为 204,800 bytes 原始候选上下文和 50,000 startup active tokens。启动 active-token 数按
Capsule bytes / 4 估算，只用于同口径比较，不等同于供应商计费 Token。

## Token 遥测边界

2026-10-09 14:22 +08:00 至 2026-10-12 23:59:59 +08:00 的本地 session 元数据统计为：

- 实现侧：raw 1,311,838,087；cached input 1,272,646,272；active 40,816,917；output 5,202,872；reasoning 1,625,102。
- 独立 review：raw 26,766,703；cached input 21,672,448；active 5,127,039；output 84,962；reasoning 32,784。
- 会话分类：main 44、subagent 249、reviewer 201。

这些数字按本机 Develop 工作区归集，是包含其他开发项目的上限，不是 Survey 的精确项目账单；
不能与计划中较窄会话谱系的历史基线直接相减。预计后续 active Token 降低 35%-45% 仍是预测，必须在两个
代表性产品 Milestone 完成后按明确项目归属重新测量。

## 安全与恢复

- Evidence key 绑定 repository、HEAD、命令、环境、输入与策略摘要；不确定、损坏或 schema 未知时真实重跑。
- strict HEAD 与最终验收证据不跨提交复用；GitHub import 校验仓库、workflow、commit、job、结论、artifact digest 与 provenance attestation。
- 同 key 并发使用原子租约；只有租约过期、完成证据无效且本机 owner PID 不存活时才允许接管。
- 完整日志只保存在 ignored runtime；成功摘要不含 stdout/stderr，失败诊断脱敏、去重且有字节上限。
- Obsidian 是 marker 管理的单向投影；Vault 不可用时记 pending，恢复后重放，机器块被人工修改时失败关闭。

## 遗留风险与恢复点

- 当前宿主未执行 Linux wrapper 的 8 个权限夹具；它们继续由 GitHub Linux job 的 `--fail-on-skip` 门禁负责。
- 当前 shell 缺少 Docker、Node、Java 21 与 Codex，因此本轮不重复运行产品业务 Gate；这些工具缺口不会被缓存为通过证据。
- 当前受控 Python 缺少 `PyYAML`，未运行完整 Release native Python suite；release-blocking 入口与环境契约已有精确测试，真实生命周期继续由 GitHub Release 的 Ubuntu 22.04/24.04、amd64/arm64 阻断矩阵执行。
- Wave 2 工作区仍有大量未提交品牌资产实现，保持原样且不纳入本控制平面交付提交。
- 下一恢复点固定为 Wave 2 Task 2：PHPUnit 先行，然后品牌图片发布/预览在 MySQL 和 PostgreSQL 上完成真实双库验证；通过后才进入 Task 3。
