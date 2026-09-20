# ADR 0004：单仓库组织

- 状态：已决定（用户，2026-09-18）
- 关联：总方案 §3.2（原建议“引擎仓库＋平台 monorepo”两仓）

## 决定

所有代码与文档放在同一个仓库，不拆分平台仓库；网络访问不使用代理。

2026-09-20 用户决定把仓库切换到 **`lsgoodlionel/star-survey`**（remote `origin`，唯一分支 `main`）。原 fork `lsgoodlionel/LimeSurvey` 保留为 remote `limesurvey-fork`，只用于对比上游和合并升级。由于本地是浅克隆、GitHub 拒绝推送浅历史，新仓库的根提交是 LimeSurvey 7.1.2 快照（注明上游提交 `4c20c680`），其后接平台提交；文件内容与切换前逐字节一致。

## 布局

| 内容 | 位置 | 理由 |
|---|---|---|
| 引擎源码 | 原位置，不改动 | 保持可与上游合并 |
| 引擎插件 | `plugins/Mjy*` | 引擎只从 `plugins/`、`upload/plugins/`、核心目录加载插件 |
| 引擎主题 | `themes/survey/zh-business`、`themes/question/mjy-*`（后续） | 引擎主题加载路径 |
| 平台服务、契约、测试、部署、文档 | `platform/` | 与引擎代码物理隔离，便于识别 GPL 边界与后续拆分 |
| 核心补丁登记 | `platform/patches/`（出现第一个补丁时创建） | 每个补丁的动机、影响文件与回归证据 |

## 影响

- `main` 分支上的提交分两类：只动 `platform/`、`plugins/Mjy*` 的平台提交；动引擎源码的核心补丁（必须登记）。与上游合并时只需关注后者。
- 平台后续的 Java/React/Python 服务放在 `platform/services`、`platform/apps`、`platform/workers`。若日后需要拆仓，`platform/` 可整体迁出。
- 上游仓库 `.gitignore` 忽略 `docker/`，因此部署文件放在 `platform/deploy/`。
- 上游 `.gitignore` 忽略 `plugins/*`（仅保留 Demo），已加一行例外 `!/plugins/Mjy*/`。这是对上游文件的唯一改动，合并上游时注意保留。
