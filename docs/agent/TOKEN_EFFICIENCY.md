# Token 高效工程操作手册

本项目以“质量不降级”为前提降低重复上下文、重复 Gate、成功日志与无效修复所消耗的 Token。权威事实仍按 Git、代码、测试、Harness 状态、批准设计与计划排序，Obsidian 和历史会话不能提升交付状态。

## 默认流程

1. 新 Agent 只读取生成的 Context Capsule 与任务增量；胶囊缺少具体事实时才按来源清单定点读取。
2. 测试、Lint、类型检查、构建和 Docker 验证在本地或 GitHub Actions 执行，成功只返回结构化摘要。
3. 失败日志先脱敏、去重并压缩到 4 KiB 以内；原始日志只保存在 ignored runtime 或 GitHub 原生日志中。
4. 相同证据键的确定性 Gate 可复用；严格 HEAD Gate、发布候选和生产验收不得跨提交复用。
5. 并发相同 Gate 由租约合并为一次执行；同一失败指纹超过预算后停止并请求人工判断。
6. Milestone、暂停、阻塞、提交、Review 或验收状态实质变化时，才同步 Obsidian 机器块。

## 命令

```sh
scripts/agent-harness token-report --since <RFC3339> --until <RFC3339> --format json
scripts/agent-harness configure-memory --vault <absolute-local-path>
scripts/agent-harness memory-status --json
scripts/agent-harness sync-memory --run-id <uuid> --json
scripts/agent-harness install-global-policy --codex-home "$HOME/.codex" --dry-run
```

`install-global-policy` 只维护明确 marker 包围的三个区块；dry-run 无差异后才视为已安装。全局文件不保存 Survey 绝对路径、动态进度、Token 数或秘密。

## 诊断与指标

- `rawTokens` 为供应商累计口径，`cachedInputTokens` 为缓存输入，`activeTokens` 为未缓存输入加输出与 reasoning。
- 主会话按时间窗累计差值统计；subagent 与 reviewer 按最终计数统计，reviewer 不计入实现成本。
- 重点指标包括 Gate 执行/复用/去重次数、失败指纹数、暴露诊断字节、胶囊字节和启动 active Token 估算。
- 指标异常先检查证据键、租约、摘要器和胶囊来源；不得通过跳过测试、放宽断言或输出原始日志“优化”。

## 故障恢复

- 缓存不可信：关闭该项复用并真实执行 Gate。
- 租约过期：仅在所有者失活且超时后接管，保留接管记录。
- Obsidian 不可用：记录待同步事务，继续本地开发，恢复后重放。
- 机器块被人工修改：停止覆盖并报告冲突。
- GitHub 证据身份、SHA、job、结论、摘要或 Artifact digest 不匹配：拒绝导入。
