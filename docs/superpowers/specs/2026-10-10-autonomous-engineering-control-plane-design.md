# 自治工程控制平面设计

日期：2026-10-10

状态：已于 2026-10-10 获用户书面确认

## 1. 背景与现状

本项目正在持续推进问卷平台的前后端产品化、生产部署和后续纵向波次。随着任务跨度、并行开发数量和运行时间增加，
仅依赖对话历史会放大上下文漂移、重复开发、错误累积、错误完成声明和 Git 状态失控风险。

仓库已经具备可复用的工程基础：

1. `docs/superpowers/specs/` 与 `docs/superpowers/plans/` 保存设计和实施计划。
2. `.superpowers/sdd/` 中已有部分任务进度和实现报告，但部分记录会落后于实际提交，不能单独作为当前事实。
3. `.github/workflows/platform-quality.yml` 已覆盖前后端、产品化、生产和端到端质量门。
4. Docker Compose、发布脚本、GitHub Release 和本地工作树流程已经形成，不能由新系统重复实现或绕过。
5. 仓库根目录当前没有项目级 `AGENTS.md`，`.agents/` 和 `.codex/` 又会被现有 `.*` 忽略规则排除。

因此，本设计不创建一套平行的项目管理系统，而是在现有文档、测试、CI、Git 和 Docker 之上增加一个可版本化、可恢复、
可验证的“自治工程控制平面”。

## 2. 目标与非目标

### 2.1 目标

1. 将长期有效规则、当前执行状态和验证证据从对话中外置到仓库与本地状态目录。
2. 通过确定性 Harness 执行“准备、开发、验证、修复、暂停、恢复、收尾”状态机。
3. 根据实际改动范围选择质量门，禁止未验证代码进入下一个 Milestone。
4. 检测重复失败、无进展循环、计划漂移、分支漂移和受保护路径越界，并按阈值主动暂停。
5. 允许通过 `codex exec` 在隔离工作树中进行无人值守的单 Milestone 执行，同时保留最小权限和人工升级边界。
6. 保存可审计、脱敏、可追溯的运行历史，使新会话能够从仓库事实恢复，而不是猜测历史。
7. 与现有 CI、GitHub、Docker、发布流程和 Obsidian 交付闭环保持一致。

### 2.2 非目标

1. 不建设独立的 Jira、项目管理平台、任务队列服务或常驻 Web 控制台。
2. 不允许 Agent 绕过沙盒、审批、分支保护、CI、代码审查或生产部署确认。
3. 不把“所有测试均通过”解释为每次小改动都运行全仓库全部测试；质量门由改动范围和风险矩阵决定。
4. 不保证在需求模糊、验收标准缺失或遗留系统没有可用验证手段时继续无人值守开发。
5. 不在状态、诊断、提交或文档中保存令牌、密码、私钥、Cookie、完整证书或敏感环境变量值。
6. 不以固定 200 行作为提交正确性的替代指标；提交边界由业务职责、风险和可审查性决定。

## 3. 设计原则

### 3.1 单一事实源

- `AGENTS.md`：项目级强制规则和入口索引。
- `docs/superpowers/specs/`：已审批设计事实。
- `docs/superpowers/plans/`：已审批实施计划事实。
- `docs/agent/PROJECT_MEMORY.md`：长期有效的项目工程事实与决策。
- `var/agent-harness/runs/`：当前运行的机器状态与原始证据，不提交 Git。
- `docs/agent/run-history/`：完成或暂停后的脱敏摘要，随代码提交。

根目录不手工维护另一份完整 `Prompt.md`、`Plan.md` 或 `Documentation.md`。如外部工具需要这些名称，只允许由 Harness
生成只读兼容视图，内容为权威文件的路径、摘要和校验和；兼容视图不得成为新的事实来源。

### 3.2 规则与执行分离

计划描述“做什么”，质量门定义“如何证明”，Harness 决定“是否允许状态前进”。计划正文、网页内容、构建日志和测试输出
均视为不可信数据，不能直接转化为任意 Shell 命令。

### 3.3 最小权限

无人值守执行默认使用独立 Git worktree 和 `workspace-write` 沙盒。命令从版本化白名单选择。禁止 Harness 调用
`--dangerously-bypass-approvals-and-sandbox`、`--dangerously-bypass-hook-trust` 或等效无限权限选项。

### 3.4 证据先于声明

状态只有在对应命令实际执行、退出码成功、证据文件写入且当前 Git HEAD 与状态一致时才能转为 `completed`。代码存在、
历史报告、模型文字说明和过期 CI 结果均不能单独证明完成。

## 4. 文件布局

```text
AGENTS.md
docs/agent/
  AUTONOMY.md
  PROJECT_MEMORY.md
  GATE_MATRIX.yaml
  PROTECTED_PATHS.yaml
  STATE_SCHEMA.json
  run-history/
    README.md
tools/agent-harness/
  README.md
  agent_harness/
    cli.py
    config.py
    state.py
    git_guard.py
    gate_runner.py
    diagnostics.py
    codex_adapter.py
  schemas/
  tests/
scripts/
  agent-harness
var/agent-harness/runs/
.github/workflows/agent-governance.yml
```

`var/agent-harness/` 加入 `.gitignore`。其中可包含未脱敏命令输出，因此不得提交。`run-history` 只接收经过脱敏器处理的
摘要和证据索引，不复制完整原始日志。

## 5. 规则控制层

### 5.1 `AGENTS.md`

根规则保持简洁，只包含每次任务都必须生效的内容：

1. 事实读取顺序和按任务读取原则。
2. 复杂功能必须经过设计审批、书面复核和实施计划审批。
3. 每次改动必须经过与范围匹配的测试、Lint、类型检查、构建或运行验证。
4. 禁止修改的路径、依赖审批、秘密处理和生产边界。
5. 工作树、分支、提交、推送、PR 和部署规则。
6. Harness 状态机、重试阈值和升级协议入口。
7. 代码、仓库文档、GitHub 和 Obsidian 的交付闭环。

具体命令、长篇流程和易变路径不复制到 `AGENTS.md`，而是链接到 `docs/agent/`，避免每次会话注入过量上下文。

### 5.2 `AUTONOMY.md`

记录完整 SOP：任务准入、Milestone 粒度、无人值守条件、人工接管条件、恢复过程、诊断格式、发布边界和事故处理。

### 5.3 受保护路径

`PROTECTED_PATHS.yaml` 支持以下策略：

- `deny`：Harness 立即拒绝并暂停，例如真实秘密、私钥和本地凭据文件。
- `approval_required`：必须有人类批准，例如生产 Compose、发布工作流、数据库不可逆迁移和认证授权核心。
- `review_required`：允许实现，但完成前必须经过独立代码审查。
- `generated`：只能由指定生成命令修改，禁止手工编辑。

规则按路径和操作类型匹配，并由单元测试保证优先级稳定。路径策略不能通过计划文本覆盖。

## 6. 状态模型

### 6.1 运行状态

每个运行目录使用 UUID，核心文件为 `state.json`、`events.jsonl`、`diagnostics.md` 和 `evidence/`。`state.json` 至少包含：

- `schemaVersion`
- `runId`
- `planPath` 与 `planSha256`
- `worktreePath`、`branch`、`baseCommit`、`headCommit`
- `milestoneId`、`milestoneTitle`
- `status`
- `attempts.total` 与 `attempts.byFingerprint`
- `lastFailureFingerprint`
- `requiredGates` 与每个 Gate 的状态、退出码、开始时间、结束时间和证据路径
- `changedPaths`
- `decisions`
- `createdAt`、`updatedAt`

状态写入采用临时文件、`fsync` 和原子替换，避免进程中断留下半个 JSON。事件日志仅追加，每条记录含前一条事件摘要，
便于发现手工篡改或截断。

### 6.2 状态机

```text
planned -> active -> verifying -> completed
                    |       ^
                    v       |
                 repairing -+

任何活动状态 -> paused
不可恢复冲突 -> blocked
```

- `planned`：已绑定已审批计划和 Milestone，尚未执行。
- `active`：允许执行该 Milestone 范围内的代码修改。
- `verifying`：冻结功能扩展，仅允许测试和针对失败的最小修复。
- `repairing`：存在已分类失败，允许在预算内修复。
- `completed`：全部必需 Gate 通过并生成脱敏完成摘要。
- `paused`：达到阈值或需要人类决策，可在条件修复后恢复。
- `blocked`：计划、权限、架构或外部依赖冲突，不能自动恢复。

Harness 拒绝非法跳转，例如 `active` 直接标记 `completed`、失败 Gate 后进入下一 Milestone，或修改计划校验和后继续旧运行。

## 7. Harness 命令与职责

首版使用 Python 3 标准库实现，避免为控制平面引入新的运行时依赖。统一入口为 `scripts/agent-harness`。

```text
agent-harness doctor
agent-harness init --plan <path> --milestone <id>
agent-harness status [--json]
agent-harness next
agent-harness gate [--profile <name>]
agent-harness record-decision --type <type> --summary <text>
agent-harness pause --reason <code>
agent-harness resume
agent-harness run-codex [--max-cycles N]
agent-harness finalize
```

### 7.1 `doctor`

检查 Git 仓库与 worktree、分支、工作区状态、磁盘空间、Docker、Node 22、Java 21、Python、必需文件、端口冲突和相关
命令可用性。检查只读执行；自动清理容器、删除目录、终止进程或修改环境必须单独授权。

### 7.2 `init`

验证计划存在且处于已审批状态，计算计划摘要，解析目标 Milestone，匹配保护路径和质量门，创建原子状态。若工作区存在
无法归属的改动、分支错误或计划没有可判定验收标准则拒绝启动。

### 7.3 `gate`

根据 Git diff 和 `GATE_MATRIX.yaml` 计算必需 Gate，使用无 Shell 拼接的参数数组执行命令，逐项保存退出码和证据。
开发者可显式增加 Gate，但不能移除自动推导出的 Gate。

### 7.4 `run-codex`

通过适配器调用本机 `codex exec`，首版固定：

- 在已验证的独立 worktree 中执行。
- 使用 `--sandbox workspace-write` 和 `--approve-for-me`。
- 使用 `--strict-config`、`--json` 和 `--output-schema` 约束输出。
- Prompt 仅包含目标 Milestone、权威文件路径、允许范围、当前失败摘要和剩余预算。
- 每一轮重新读取磁盘状态，不依赖模型自行记住前一轮结论。
- 使用 Codex 会话 ID 支持同一 Milestone 的有限恢复，不跨 Milestone复用长会话。

适配器不得传递环境中的秘密值，不得把原始模型事件直接提交到仓库。CLI 不可用、未认证或版本不满足能力检查时，
Harness 进入 `paused`，而不是降级到无限权限或其他未批准的执行器。

## 8. 质量门矩阵

`GATE_MATRIX.yaml` 以仓库路径为输入，映射到固定命令和超时。例如：

| 改动范围 | 必需 Gate |
|---|---|
| 管理端前端 | Lint、TypeScript、组件测试、生产构建 |
| Java 平台服务 | 单元/集成测试、编译、架构约束 |
| Python 网关或工具 | 对应测试、静态检查、启动/健康检查 |
| Compose、安装升级脚本 | 配置解析、Shell 检查、安装/升级/卸载演练 |
| 发布工作流 | 工作流语法、制品校验、版本一致性检查 |
| 跨前后端用户流程 | API 契约测试、E2E、浏览器关键路径验证 |
| 仅文档 | 链接、路径、状态和事实一致性检查 |

多个范围同时修改时取 Gate 并集。涉及认证、租户隔离、数据库迁移、生产部署或发布链时自动提升到高风险配置，并要求独立审查。

## 9. 自愈与升级协议

### 9.1 失败指纹

Harness 对退出码、失败测试名、首个稳定错误位置和规范化错误文本计算指纹。时间戳、临时目录、随机端口和颜色控制符在
计算前移除，避免同一错误被误判为不同问题。

### 9.2 自动修复预算

- 同一失败指纹最多自动修复 3 次。
- 单个 Milestone 最多进行 5 个失败修复周期。
- 连续 2 轮 Git diff、失败指纹和 Gate 结果均无实质变化，判定为无进展。
- 修复导致改动越过 Milestone 范围、触及 `approval_required` 路径或新增依赖时立即暂停。
- 测试基础设施本身失败时先分类，不得通过删除、跳过或放宽断言制造通过。

### 9.3 Diagnostics

暂停或阻塞时生成：

1. 当前计划、Milestone、分支、HEAD 和改动文件。
2. 失败 Gate、稳定指纹和最小相关日志。
3. 已尝试修复及每次结果。
4. 自动恢复为何停止。
5. 建议的人类决策和准确恢复命令。

生成前对 URL 凭据、Authorization、Cookie、常见 Token、密码、私钥块和敏感环境变量执行脱敏。脱敏测试使用伪造秘密，
不得在测试夹具中放真实值。

## 10. 状态恢复与漂移处理

`resume` 必须重新运行 `doctor` 并核对：

- 当前工作树、分支和仓库身份与状态一致。
- 当前 HEAD 是记录 HEAD，或能解释为本运行产生的后继提交。
- 计划路径和摘要没有变化。
- 未出现状态之外的新改动。
- 已通过 Gate 的证据仍对应当前代码；HEAD 变化后相关 Gate 自动失效。

若只存在可证明的状态文件落后，Harness 根据 Git 和事件日志重建派生字段并记录 `reconciled` 事件。无法证明归属的代码改动、
计划变化、分支切换或历史改写一律暂停，禁止静默覆盖。

## 11. Git、PR 与发布边界

1. 每个自治任务使用独立工作树和任务分支，不在共享 checkout 中长时间运行。
2. 一个 Checkpoint 对应一个可验证的职责边界，不按固定行数机械拆分。
3. 提交前检查 staged 文件、保护策略、秘密扫描和必需 Gate。
4. Harness 不执行 force push、自动 rebase、自动合并、标签覆盖或生产部署。
5. 普通 push 和 PR 创建仅在项目授权范围内执行；认证、保护分支或远端冲突时暂停。
6. PR 描述包含计划、Milestone、验证证据、风险、迁移/回滚和遗留项。
7. 生产、认证、迁移和发布链改动必须有人类最终 Review，不因测试通过自动合并。

## 12. CI 治理

新增 `agent-governance.yml`，对 Harness 与 Agent 产生的变更执行：

- `AGENTS.md` 和控制平面配置语法检查。
- 状态 Schema、Gate 矩阵和保护路径规则测试。
- Harness 单元测试和临时 Git 仓库集成测试。
- 禁止选项和危险命令静态检查。
- 文档中的路径、命令和权威来源一致性检查。
- 运行摘要脱敏测试。
- 计划、实现报告和能力状态之间的漂移检查。

该工作流只提供治理结论和证据，不自动批准或合并 PR。

## 13. 永久项目记忆

`PROJECT_MEMORY.md` 记录稳定且可验证的信息：

- 仓库用途、目录边界和主要技术栈。
- 权威需求、设计、计划和进度文件位置。
- 标准测试、构建、Docker 和发布入口。
- 已确认的架构决策及其日期和依据。
- 已知高风险区域、历史事故根因和仍有效的规避规则。
- GitHub 仓库、正常交付分支策略和 Obsidian 同步入口，但不记录凭据。

临时任务状态、未经验证的推测、当前端口、一次性错误和个人访问令牌不得进入永久记忆。每条可变事实标明验证日期；
新会话仍需重新检查 Git、容器、服务和远端状态。

## 14. 测试与验收

### 14.1 单元测试

- 配置解析、Schema 校验和非法状态迁移。
- 路径规则优先级、路径逃逸和符号链接边界。
- Gate 并集、超时、退出码和证据失效。
- 失败指纹稳定性、重试计数和无进展检测。
- 日志脱敏、事件链和原子状态写入。
- Codex 结构化输出解析和非法响应拒绝。

### 14.2 集成测试

在临时 Git 仓库中覆盖：

1. 初始化、修改、Gate 通过、Finalize 和历史摘要生成。
2. 同一失败连续 3 次后暂停。
3. 总失败周期达到 5 次后暂停。
4. 进程在写状态前后被终止并成功恢复。
5. HEAD、分支、计划或工作区发生漂移时拒绝继续。
6. 触及受保护路径、引入依赖和出现未归属改动时暂停。
7. 日志中包含伪 Token、密码和私钥时，提交摘要不包含原文。
8. Codex CLI 不可用、未认证、输出不合 Schema 和超时时安全停止。

### 14.3 当前仓库演练

- 使用一个只改测试夹具的成功演练验证完整闭环。
- 使用一个故意失败的测试验证修复预算和 Diagnostics。
- 使用一个计划摘要变化场景验证漂移拦截。
- 使用 Docker/Node/Java 缺失模拟验证 `doctor` 的可操作报告。
- 演练不得修改生产秘密、创建正式 Release 或部署服务器。

## 15. 分阶段交付

### 阶段 A：规则与状态基础

建立 `AGENTS.md`、`docs/agent/`、Schema、状态机、`doctor/init/status/resume/finalize` 和测试。

### 阶段 B：质量门与自愈

接入 Gate 矩阵、保护路径、失败指纹、重试预算、无进展检测、Diagnostics 和脱敏。

### 阶段 C：Codex 无人值守适配

接入受控 `codex exec`、结构化响应、有限循环、会话恢复、超时和故障注入测试。

### 阶段 D：CI 与交付闭环

接入 GitHub Actions、Checkpoint/PR 摘要、仓库文档、GitHub 推送和 Obsidian 同步。

### 阶段 E：真实项目试运行

先选择低风险、验收明确的单个 Milestone 试运行。通过人工复盘后，才逐步允许中风险前后端任务使用无人值守模式。

## 16. 与当前产品开发的关系

该控制平面是后续开发的工程保障，不替代已经确认的产品路线。控制平面完成并试运行后，恢复
`2026-10-10-business-dashboard-v1-design.md` 的书面复核与实施计划，再按既定七个纵向波次推进前后端产品化。

为了避免基础设施无限扩张，首版以“能够安全驱动一个真实 Milestone 完成”为准出条件，不建设可视化控制台或外部调度服务。

## 17. 准出条件

以下条件全部满足，才能称“自治工程控制平面首版完成”：

1. 新会话能从 `AGENTS.md`、已审批计划和项目记忆恢复正确上下文。
2. Harness 能在独立工作树初始化、执行质量门、保存证据、暂停和恢复。
3. 重复失败、无进展、计划漂移、Git 漂移和受保护路径越界均有自动测试。
4. 原始状态不进入 Git，脱敏历史能够追溯到计划、分支、提交和测试证据。
5. `codex exec` 只能在受控沙盒和有限循环中执行，危险绕过选项有静态与运行时双重拦截。
6. CI 能独立验证控制平面规则和 Harness 测试。
7. 成功、失败和中断恢复三类演练全部通过。
8. README、进度文档、GitHub 提交和 Obsidian 项目记录完成同步。
