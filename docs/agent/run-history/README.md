# 脱敏运行历史

本目录保存完成、暂停或阻塞后的可提交摘要。原始状态和日志留在 ignored 的 var/agent-harness/runs/<UUID>/。
当前只提供格式约定；后续 finalize 和脱敏器交付并验证之前，不自动复制原始输出到这里。

使用 <日期>-<runId>.md 命名。每份摘要包含：

- 运行 UUID、日期、状态、计划相对路径与 SHA-256、Milestone ID/标题。
- 仓库/worktree、分支、baseCommit、最终 headCommit、提交或 PR 的可追溯链接。
- 改动文件、已完成验收与各 Gate 的参数数组、cwd、退出码、时间和证据索引。
- 失败指纹、修复次数、决定记录、停止原因、遗留问题、下一步和恢复命令。
- 自审/独立审查结论，以及仓库文档、GitHub、Obsidian 的实际同步状态。

提交前必须脱敏 URL 凭据、Authorization、Cookie、JWT/Token、密码、私钥和敏感环境变量，检查最终 staged 内容。
不得复制完整原始日志、模型 JSONL、环境变量转储、证书或秘密配置。证据索引只说明安全本地位置，不将内容打包进 Git。
历史摘要仅证明所记录提交与当时命令；不能替代新 HEAD、新环境或恢复时重新验证。
