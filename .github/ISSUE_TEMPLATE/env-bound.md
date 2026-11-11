---
name: 环境绑定任务
about: 只能在特定环境执行的工作，例如 Owner 的 Mac、真实账号或生产主机
title: ""
labels: ["env:mac"]
---

## 环境

- 需要的环境：<Mac 本机 / 真实账号 / 生产主机 / 其他>（不是 Mac 时，把标签换成对应的环境）
- 执行者：<能访问该环境的 Owner 或 Agent>

## 步骤

1. 基于最新 main 执行（写明精确命令）：
   ```bash
   git fetch origin && git checkout --detach origin/main
   ```
2. …

## 需要回贴的结果

- main 的精确提交（`git rev-parse HEAD`）
- 命令的完整输出和退出码
- **不要贴**：秘密、令牌、个人信息、本机绝对路径或私有原始材料；必要时只贴摘要或 SHA-256

## 判断标准

- 成功：…
- 失败时：…（不要手改结果，回贴原样输出）
