# 多 Agent / 多人协同开发工作流（star-survey 实施版）

> 状态：**草案 v1（2026-11-11）**，由 Claude Code 起草，待 Codex 补充、Owner 批准。
> 关系：[`AGENTS.md`](../../AGENTS.md) 是**硬规则**（必须遵守）；本文是**协作协议**（怎么分工、交接、留痕）。两者冲突时以 `AGENTS.md` 为准。
> 来源：通用多 Agent 协作方案，加上本仓库 2026-11-02 至 11-11 期间 Claude Code 与 Codex 协作推进 Wave 0–6 和首个 Release 的实际经验（#37–#41、#83–#113）。

---

## 0. 一句话总结

**GitHub 是唯一事实来源；按"责任区"分工，而不是逐单抢任务；每个结论都附上精确的提交和运行证据；所有不可逆或对外的动作，都要经过人类闸门和事前核查。**

---

## 1. 目标与原则

**目标**：在同一个 GitHub 仓库里，让人类 Owner 与多个 AI Agent（目前是 Codex 和 Claude Code，今后可能更多）协同推进。要做到：任务有归属、过程可追溯、状态可观测、交接无歧义、对外动作可控。

| # | 原则 | 本项目里的含义 |
|---|---|---|
| P1 | **单一事实来源** | 状态只认 GitHub 上的 Issue、Project、PR 和 Actions。本地文件、会话记忆、口头说法都不算数 |
| P2 | **责任区优先** | 按领域划定长期负责人（见 §3）。只有跨区或临时任务才逐单认领 |
| P3 | **证据优先** | 每个"通过、完成、已修复"都要附上精确的 head SHA、Actions run 链接和测试名。**CI 全绿不等于验收通过** |
| P4 | **先写失败测试** | 修复和功能都先写一个会失败的测试（`AGENTS.md`）。不得删除、跳过或放宽断言来换取通过 |
| P5 | **并发隔离** | 每个执行者使用独立的 worktree 和分支；同一组文件同一时间只有一个写入者 |
| P6 | **人类闸门不可代签** | 合并、打 tag、发布、删除、改权限、人工验收记录，都要 Owner 明确授权。Agent 不得编造测试人或复核人，不得自我批准 |
| P7 | **不可逆动作先核查** | 打 tag、发 Release、合并、删除之前，先跑一遍 §9 的核查清单 |
| P8 | **历史不改写** | 失败的 tag 和候选保留并标为不合格，不移动、不复用。Issue 正文更新时在顶部加"当前状态"，原文保留为历史 |
| P9 | **异步优先** | 进展、阻塞、交接写在 Issue 评论里；跨任务的设计讨论放到 Discussions；不用会话内的口头约定来推进 |

---

## 2. 本仓库现状基线（2026-11-11 核实）

方案必须建立在这些事实上；事实变化时同步更新本节。

- **身份**：Codex 和 Claude Code 都通过 Owner 的账号 `lsgoodlionel` 操作 GitHub。由此带来三个后果：
  - GitHub 的 Assignee、评论作者、PR 作者**无法区分**是哪个 Agent；
  - 同一账号**不能 approve 自己的 PR**，所以 Agent 之间的审查只能以"审查评论"的形式留痕；
  - 发验收、批准类评论时，会显示为 `OWNER` 身份。这类评论必须先得到 Owner 对正文的逐字确认（见 §8）。
- **仓库**：公开仓库；`main` **没有分支保护**；Discussions 已开启。
- **Project**：Project 1 有两个关键字段：`Status`（待办 / 开发中 / 验收中 / 完成）和 `Wave`（Wave 0–6 / 延期）。
- **标签**：现有 `bug`、`documentation`、`enhancement`、`accessibility`、`Needs code review`、`Needs testing`、`Has conflicts` 等，还没有 Agent 或环境类标签。
- **现有自动化**：
  - `labeler.yml`：PR 标签与冲突管理；
  - `review-labeler.yml`：approve 后更新标签；
  - `codex-autofix.yml`：手动触发，最多修 5 个文件、最多两次，合并由人工完成。
- **治理约束**：`docs/agent/PROJECT_MEMORY.md` 记录了 **2026-10-28 Owner 决定移除自建控制平面及其治理 CI**。因此本方案里所有新增的 Actions 自动化都是**可选**的，必须由 Owner 重新批准后才能落地（见 §12）。
- **上游文件**：根目录的 `CONTRIBUTING.md` 和 `.github/pull_request_template.md` 是 LimeSurvey 上游原件，**不改**。本项目自己的约定写在 `docs/agent/` 下。
- **执行环境**：
  - Owner 的 Mac 上有仓库外的方案文档（例如 02 需求矩阵）；
  - Claude Code 当前运行在 Windows 上，有 MAX_PATH 限制，且没有 Docker 和 POSIX 环境；
  - Linux 相关的验证以 GitHub Actions 为准。

---

## 3. 角色与责任区

### 3.1 角色

| 角色 | 职责 | 不能做的事 |
|---|---|---|
| **Owner（人类）** | 定范围和优先级；批准合并、tag、Release 和生产操作；签署人工验收；处理权限和删除类操作 | — |
| **Codex** | 按责任区实现、修复、验收对账；在 Issue 中提出跨区需求 | 未经授权不合并、不打 tag、不发布；不改其他责任区的文件 |
| **Claude Code** | 同上；另外负责 Release 线；独立审查其他 Agent 的 PR | 同上 |
| **环境绑定执行者** | 需要特定机器的任务（标记为 `env:mac`），由 Owner 本人或在 Mac 上运行的 Agent 执行 | — |
| **独立审查者** | 由**非作者**的 Agent 或人审查 PR，以审查评论留痕 | 不能审查自己写的 PR |

### 3.2 责任区（Lane）

这是实际运行中形成的划分，今后可以落成机器可读的 `.github/agent-lanes.yml`（§11 Q3）。

| 责任区 | 负责人 | 典型路径 | 典型 Issue |
|---|---|---|---|
| Release 工作流、tag、候选登记、发布说明、Release 发布 | **Claude Code** | `.github/workflows/release.yml`、`.github/release/`、`platform/deploy/production/release/` | #34 |
| 生产控制器、受控恢复、原生生命周期、安装目录安全 | **Codex** | `platform/deploy/production/surveyctl.py`、`backup.py`、`restore.py` 及其测试 | #33、#108、#109 |
| Wave 1/3/4 验收对账与产品修复 | **Codex** | `platform/apps/admin-web/`、`platform/services/business/` 的对应功能 | #38、#39、#40 |
| 下一版本范围盘点 | **Codex**（等 Owner 定范围） | `platform/docs/productization/` | #106、#107 |
| 生产 Compose 和 Caddy 静态加固 | 最近一次由 Claude Code 负责（#104） | `platform/deploy/production/compose.yml`、`Caddyfile` | #37 |
| 需求追溯（reqtrace） | 由 #112 指定 | `platform/tools/traceability/` | #112 |
| 人工验收、发布批准、生产与公网 | **Owner** | — | #41、#34 |

**规则**：
1. 在别人的责任区里发现问题时，**不要直接改**。新开一个 Issue，写清问题、利用条件、建议修法和验收标准，加上责任区标签，放进看板，并在相关 Issue 里互相引用（例如 #109）。
2. 需要接手别人责任区里的某项工作时，必须由对方或 Owner 在 Issue 里**明确移交**（§7）。
3. 责任区可以由 Owner 调整，调整后更新本表。

---

## 4. 载体分工

| 载体 | 用途 | 本项目约定 |
|---|---|---|
| **Issue** | 任务、缺陷、阶段记录、交接、跨区请求 | 阶段类 Issue（Wave）的正文顶部维护「## 当前状态（日期，对照 `main` `<sha>`）」，原文保留在「历史记录」下。过程写在评论里 |
| **Sub-issue** | 把大任务拆成能各自交接的子任务 | 每个子任务都有自己的责任区标签和验收标准 |
| **PR** | 代码变更 | 先 Draft，就绪后改为 Ready。正文写清：问题、变更、先失败再通过的证据、本地和 CI 验证、范围外事项、关联 Issue（阶段 Issue 用 `Refs #N`，不要用 `Closes`） |
| **Project** | 状态流转 | 新增的 Issue 和 PR 当天加入看板，并填好 `Status` 和 `Wave`。PR 合并后改为「完成」。阶段 Issue 由 Owner 决定何时进入「完成」 |
| **Discussions** | 跨任务的设计决策、方案选型 | 结论要回填到对应 Issue 或文档，Discussions 本身不作为执行依据 |
| **Actions** | 质量门和证据来源 | 证据只认**精确 head** 的 run。push 到 main 和 pull_request 触发的结果分开记录 |
| **Release** | 不可变制品 | 正文和资产依据**候选自带的数据**生成，不依据执行晋升的源码（教训见 §13-④） |

**沟通分流**：任务进展、阻塞、交接写在 Issue 评论；代码细节写在 PR 评论；设计选型放到 Discussions；需要 Owner 决定的事项在 Issue 里加 `needs:owner` 标签，并在会话里提醒 Owner。

---

## 5. 身份与留痕约定（共用账号下的过渡方案）

在 §11 Q1 的独立身份落地之前：

1. **评论署名**：标题或首行带上「（Claude，YYYY-MM-DD）」或「（Codex，YYYY-MM-DD）」，例如 `## #108 独立审查（Claude，2026-11-11，精确 head 133933f76c）`。
2. **提交**：作者统一用仓库已登记的身份。在 trailer 里写明 Agent，例如 `Co-Authored-By: Claude …`。不得使用虚构的作者身份。已有历史保持不变，今后统一。
3. **标签**（待新增，见 §12）：
   - `agent:claude`、`agent:codex`：当前负责的 Agent；
   - `needs:owner`：等待 Owner 决定或执行；
   - `env:mac`：必须在 Owner 的 Mac 上执行；
   - `lane:release`、`lane:controller`……：责任区；
   - `handoff`：交接中。
4. **分支**：
   - `claude/<slug>`、`codex/<slug>`：现有习惯，保留；
   - 新分支建议用 `<agent>/<issue号>-<slug>`，例如 `claude/112-vendor-matrix`；
   - 人类用 `human/<name>/<slug>`；
   - **不使用**含义不清的前缀（例如 `fix/` 看不出是谁的）。
5. **会话可追溯**：在 PR 或评论里写明工作树位置和基线 SHA。会话 ID 可以选填。

---

## 6. 标准工作流

```
① 需求/问题 → 建 Issue（问题、验收标准、责任区、标签）→ 加入 Project（待办 + Wave）
② 认领：在 Issue 评论里写「认领：范围=文件/目录；预计产出=PR」→ Status=开发中
③ 事前核查：git fetch；核对 main 的 SHA、共享 checkout 的状态、对方是否有未合并的分支动到同一组文件
④ 隔离：从 origin/main 建独立 worktree（Windows 用短路径 + core.longpaths；不在共享 checkout 里改）
⑤ 先写失败测试 → 确认失败 → 实现 → 确认通过
⑥ 本地验证；环境不一致时，「对比未改动 main 的失败集合」，证明没有引入新失败
⑦ 推送 → 开 PR（Draft 或 Ready）→ 加入 Project（验收中）→ 在 Issue 里写进展和 PR 链接
⑧ 精确 head 的 CI → 非作者做独立审查（审查评论）
⑨ Owner 授权合并 → 用 --match-head-commit 锁定已测 head 后合并 → 等合并后 main 的 CI
⑩ 回填：Issue 证据评论、Project=完成、阶段 Issue 的「当前状态」、对账表
⑪ 清理：删除本地 worktree 和本地分支；远端分支只有在 Owner 同意后才删
```

**关键细节**：
- 等 CI 时用后台监听（`gh pr checks --watch` 或 `gh run watch`），不要人工轮询。
- 合并用 `gh pr merge N --merge --match-head-commit <已测 head>`，防止合并进未经测试的提交。
- PR 落后于 main，但改动的文件不相交时，可以直接合并（合并后由 main 的 CI 兜底）。如果有相交的文件，先 rebase 或合并 main，重新跑 CI。

---

## 7. 交接协议

### 7.1 交接备注模板（写在 Issue 评论里）

```markdown
## 交接（<来源 Agent> → <接手方>，YYYY-MM-DD）
- 责任区 / 范围：<lane>；涉及文件：<路径列表>
- 当前进度：已完成 … ／ 未完成 …
- 代码位置：分支 `<branch>`，head `<sha>`；PR #N（Draft/Ready）
- 证据：CI run <链接>（精确 head）；本地验证 <命令与结果>
- 未验证 / 已知风险：…（例如「Windows 本地无法运行 POSIX 测试」）
- 阻塞：…（依赖 #N / 等待 Owner 决定 / 需要 env:mac）
- 禁止事项：…（例如「不要移动 v0.3.20-rc.1」「不要改 surveyctl.py」）
- 接手条件：在什么条件满足后接手（例如「#33 和 #109 都关闭之后」）
- 下一步建议：…
```

### 7.2 交接规则

1. **交接要有来有回**：接手方要回复「已接手（日期）」。没有回复之前，责任仍在原负责人。
2. **可从 Issue 直达代码**：交接备注里必须写明分支或 PR 和 head SHA。
3. **条件式交接**：后续步骤依赖其他前提时，写清接手条件（例如 Codex 在 #34 写的「受控恢复和 #109 闭合后，由 Release 线冻结 main 并登记新版本」）。
4. **环境绑定任务**：需要 Mac、真实凭据或外部环境的步骤，单独写成一个 Issue。给出可以照着执行的命令、需要回填的内容和判断规则，并加上 `env:mac` / `needs:owner` 标签（例如 #112）。
5. **回交**：Codex 需要 Claude 接手的事项（或者反过来），都写在 Issue 里，不依赖会话。

---

## 8. 人类闸门（Human-in-the-loop）

| 动作 | 需要的授权 | 说明 |
|---|---|---|
| 合并 PR | Owner 对**这个 PR** 的明确授权（例如「绿了就合」） | 授权只对当次有效，不能推及其他 PR |
| 打 tag、触发候选、发布 Release、修改 Release 正文 | Owner 明确授权，并且通过 §9 核查 | tag 不可移动 |
| 删除分支或数据、修改权限或设置、修改 Agent 自身配置 | Owner 本人操作，或者 Owner 明确授权 | Agent 的安全策略可能直接拦截。被拦截时不要绕过，把命令或清单交给 Owner |
| 人工验收记录（读屏、作者旅程等） | **只能由真人执行**；以 Owner 身份发评论前，Owner 要逐字确认正文 | 不得编造测试人或复核人；独立复核人必须是另一个人 |
| 生产、公网、真实凭据 | Owner | Agent 不读取、不传播秘密 |

**机器可校验的人工闸门**（推荐模式，已在 `candidate_provenance.py` 实现）：人工批准写成格式固定的评论，绑定 run、tag、commit 和 manifest SHA，再加上证据评论的 SHA256。工作流再校验三件事：评论作者必须是 OWNER，时间顺序必须对，内容必须逐字匹配。这样即使 Agent 能发评论，也绕不过「真人先关闭 Issue、再签字」这个顺序。今后有新的人工闸门，都照这个模式设计。

---

## 9. 不可逆动作的事前核查清单

**适用于：打 tag、触发发布工作流、合并、删除分支、编辑已发布内容。**

- [ ] `git fetch` 后，确认目标 SHA 就是要发布或合并的那一个，期间 main 没有变化
- [ ] 精确 head 的 CI **全部**通过（跳过的检查逐项确认是按条件跳过）
- [ ] **读一遍目标工作流**，看有没有硬阻断（`if: false`、写死的 `exit 1`、`Block … before …` 一类步骤）
  - 教训：rc20 打 tag 前没有读到 #99 加的原生阻断，结果四个平台全部失败，还白白消耗了一个版本号
- [ ] 前置登记已经完成（例如 `baseline-policy.json` 里的新版本条目）
- [ ] 依赖的 artifact 没有过期（例如 `verified-release-assets` 的有效期）
- [ ] tag 或版本号在远端还没被占用
- [ ] 人工闸门的记录已经就位并通过离线校验：先用仓库里的校验函数 dry-run，再用实时数据复核
- [ ] 失败时怎么处置已经想清楚（例如保留为不合格、换新版本号、不移动 tag）

---

## 10. 并发隔离

1. **永远不在共享 checkout 里改代码**。每项工作都从 `origin/main` 建独立 worktree。Windows 上用短路径（例如 `F:\Develop\survey\wt-<slug>`），并加上 `-c core.longpaths=true`（引擎 vendor 目录的路径超过 MAX_PATH）。
2. **stash 是所有 worktree 共用的**，不要用不带名字的 `git stash`。需要临时搁置时，用 WIP 提交。
3. **同一组文件只有一个写入者**：开工前查看对方的开放 PR 和分支（`gh pr list` 加上 `gh pr diff --name-only`）。如果有交集，在 Issue 里协调先后顺序（例如 #109 排在 #108 之后）。
4. **清理**：PR 合并后删除本地 worktree 和本地分支。远端分支统一列出清单，交给 Owner 决定。
5. **共享 checkout 里有别人未提交的改动**时，不碰、不还原，在 Issue 里说明一句即可。

---

## 11. 第七节待完善问题的具体答案

### Q1 Agent 身份与最小权限
- **目标形态**：每个 Agent 一个 **GitHub App**（或 fine-grained PAT），按仓库和权限最小化授权：
  - `contents: write`：仅限自己前缀的分支（配合 ruleset 限制，例如 `claude/**`）；
  - `pull_requests: write`、`issues: write`；
  - **不给** `administration`、`workflows` 写权限、Release 发布权限。
- **好处**：
  - 评论和提交的作者天然可以区分；
  - Agent 之间可以真正 approve 对方的 PR；
  - ruleset 可以要求「至少一个非作者的 approve」；
  - Release 发布这类敏感动作只保留给 Owner 的账号，或由 Owner 手动触发工作流。
- **过渡期**：按 §5 的署名、trailer 和标签约定执行。

### Q2 认领的原子性
- Issue 评论没有原子性。推荐 **以 git ref 作为锁**：认领时推送 `refs/claims/issue-<N>`（或空分支 `claim/issue-<N>`）。远端创建 ref 是原子操作，第二个推送者会因为 ref 已存在而失败，于是知道已经被占用。释放时删除这个 ref，需要有删除权限，或由 Owner 清理。
- **备选**：用 Actions 的 `concurrency: claim-${{ issue.number }}` 串行处理认领标签；如果同一个 Issue 同时出现两个 `agent:*` 标签，就移除后加的那个并发评论告警。
- **本项目现状**：按责任区分工，逐单认领的场景很少，**先只做约定**，不上锁。

### Q3 冲突检测与仲裁
- **检测**（轻量 Action，可选）：PR 更新时，计算它改动的文件和其他开放 PR 是否有交集，再对照 `.github/agent-lanes.yml` 判断是否越过了责任区。有风险就加 `Has conflicts` 或 `cross-lane` 标签并评论。
- **仲裁规则**：
  - **不自动回滚**，不自动 force push；
  - 先看优先级标签，再看 Owner 指定的顺序，最后按先开 PR 的优先；
  - 后合并的一方负责 rebase 并重新跑 CI。

### Q4 交接质量校验
- 可选 Action：在 `issues: labeled(handoff)` 或 `assigned` 时，检查最新一条评论是否含有 §7.1 模板的必填行：「代码位置」「证据」「未验证」「接手条件」。缺项时移除 `handoff` 标签，并评论列出缺了哪些项。注意 Action 无法阻止 Assignee 变更，只能事后纠正。

### Q5 用 Project 作 Agent 状态机
- 可以作为**观测层**，但**不能作为权威门禁**：
  - 映射关系：待办 → 开发中（认领）→ 验收中（PR Ready 或等待验收）→ 完成（合并，或 Owner 关闭阶段 Issue）；
  - PR 合并后自动改为「完成」可以自动化；
  - **阶段类 Issue 的「完成」只能由 Owner 手动设置**（CI 绿不等于验收通过）；
  - 非法跳转（例如从待办直接到完成）只告警，不拦截。

### Q6 跨平台上下文同步
- `AGENTS.md` 是所有 Agent 共同的硬规则入口（Codex 原生读取）。Claude Code 通过 `CLAUDE.md` 指向 `AGENTS.md` 和本文（待新增，一行即可）。
- 阶段 Issue 的固定结构：顶部是「当前状态」，往下是证据、未关闭项，最后是历史。评论用统一的标题格式（§5），方便机器解析。
- 每个 Agent 开工前**先读相关 Issue 的最新评论**，避免重复对账或重复开表（这是 Codex 在 #34 提的要求）。

### Q7 成本与速率
- Actions 的控制手段：
  - 重型作业（跨层、原生四平台、开发中继）**只用手动触发或打 tag 触发**（现状如此，保持）；
  - 用 `concurrency` 加 `cancel-in-progress` 取消同一分支上过期的运行；
  - 用 `paths` 过滤纯文档类 PR；
  - 每周看一次 Actions 用量。
- Agent 侧：一个 Issue 同一时间只能由一个 Agent 处理（§10-3）；等待 CI 用后台监听，不轮询；大范围搜索交给子代理，避免重复消耗上下文。

---

## 12. 落地路线图

| 阶段 | 内容 | 执行 | 前提 |
|---|---|---|---|
| **P0（立即，只靠约定）** | 本文定稿并合并；`CLAUDE.md` 指向 `AGENTS.md` 和本文；按 §5 署名，按 §7 交接，按 §9 核查 | Claude、Codex | Owner 批准本文 |
| **P1（模板与标签）** | 新增标签 `agent:*`、`needs:owner`、`env:mac`、`lane:*`、`handoff`；在 `.github/ISSUE_TEMPLATE/` 下新增「任务」「交接」「环境绑定任务」三个模板（不动上游 PR 模板，本项目用 PR 正文约定）；新增 `.github/agent-lanes.yml` | Claude 起草，Owner 批准 | — |
| **P2（轻量自动化，可选）** | Project 自动化（Issue 建立后进待办、PR 合并后完成）；Q3 冲突提示；Q4 交接校验 | 起草后由 Owner 决定 | **需要 Owner 明确推翻或细化 2026-10-28「移除治理 CI」的决定** |
| **P3（身份与保护）** | 为每个 Agent 建 GitHub App；为 `main` 建 ruleset：要求 PR、要求非作者 approve、要求通过的检查、禁止 force push；Release 发布只保留给 Owner | Owner | 已落实 Q1 |

---

## 13. 实战教训（2026-11 Wave 6 与首个 Release）

1. **CI 绿不等于验收**：阶段 Issue 一律写清「这次结论覆盖什么、不覆盖什么」。例如 rc19 只覆盖旧提交，不覆盖之后的修复。
2. **Issue 正文会过时**：#32、#37–#40 的正文都滞后于实际。解决办法是在顶部加「当前状态（对照 sha）」，原文保留。
3. **能力映射会和代码脱节**：`capabilities.json` 的 administration 已经和实际页面不一致。人工文档不能提升状态，需要另行对账。
4. **按"执行晋升的源码"渲染发布说明会出错**：用 main 的渲染器生成 rc19 的说明，写出了「recovery is currently disabled」，但 rc19 实际上仍会执行恢复。说明应当依据**候选自带、受签名约束的数据**生成（#111）。
5. **打 tag 前要读工作流**：rc20 被一道早已存在的硬阻断拦下，白白消耗了一个版本号（见 §9）。
6. **跨区问题开 Issue，不要顺手改**：审查 #108 时发现的安装目录属主问题，开成 #109 交给 Codex，没有和 #108 抢同一个文件。
7. **环境差异要用基线对照**：Windows 本地有几十个 POSIX 测试跑不了。拿「未改动 main 的失败集合」逐条对比，证明没有引入新失败，最终以 Linux CI 为准。
8. **人工签字不能代发**：VoiceOver 验收需要两个不同的人；以 Owner 身份发评论前，要先让 Owner 逐字确认正文。
9. **安全策略拦截不要绕过**：删除远端分支、修改 Agent 自身权限这类操作被拦截时，把清单和命令交给 Owner。
10. **共用账号带来的限制**：同一账号不能 approve 自己的 PR，Assignee 也区分不了是哪个 Agent。这是推动 P3 的主要原因。

---

## 14. 待 Owner 决策

1. 是否批准本文作为协作协议（P0）。
2. 是否新增 §5 的标签体系和 Issue 模板（P1）。
3. 是否重新引入轻量治理自动化（P2），以及引入哪些。
4. 是否为每个 Agent 建立独立的 GitHub App 身份，并为 `main` 启用 ruleset（P3）。
5. 责任区表（§3.2）是否需要调整。
