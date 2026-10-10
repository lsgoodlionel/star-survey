<!-- harness-delivery-status: local_validated_sync_pending -->
# 自治工程 SOP

权威设计：[已确认 spec](../superpowers/specs/2026-10-10-autonomous-engineering-control-plane-design.md)。
CLI、状态持久化、路径匹配、Gate 执行、脱敏、Codex 适配与只读 CI 已在本地实现并通过自动验证。
当前交付状态由 [HARNESS_DELIVERY.json](HARNESS_DELIVERY.json) 唯一记录；独立审查、GitHub 与 Obsidian 同步仍待完成。

## 准入与实施

1. 核对仓库身份、独立 worktree、分支、HEAD、工作区归属、工具与相关服务。
2. 读取批准设计与计划，绑定计划路径、SHA-256、Milestone 和明确验收标准。
3. 依据 GATE_MATRIX.yaml 和 PROTECTED_PATHS.yaml 确定范围、必需验证和人工批准。
4. 一个 Milestone 对应可验证职责，不按固定行数拆分；需求模糊、无验收或无验证手段时暂停。
5. TDD 完成实现后冻结功能扩展，运行全部相关 Gate；失败只能进行预算内最小修复。
6. 独立审查必需的变更，检查 staged 文件和秘密，再提交脱敏摘要与代码。

## 策略与质量门

配置均使用 YAML 1.2 的 JSON 子集，标准库 json 解析；未知字段、无效版本和歧义数据拒绝。
命令是参数数组，不经 Shell 拼接；只选版本化白名单，不能把日志或计划正文转成命令。
各 profile 的路径触发 Gate 并集；重复 Gate ID 仅执行一次。可增加 Gate，不能删自动 Gate。
Gate 的 cwd 是仓库相对路径，timeout_seconds 是正整数。初始预算是配置值，可经审批调整。
前置依赖来自现有 CI：Node 22、Java 21、Python 3.11+、相关数据库/Docker 与已锁定工具。
安装依赖、容器清理、端口调整和进程终止不属于只读 doctor，不得自动执行。
production-release profile 的测试不授权正式 Release 或生产部署。
cross-stack-e2e 包含真实浏览器与 P1 纵向流程；数据库相关场景还须遵守现有双库要求。
documentation 包含配置/路径契约、追溯和能力一致性检查；人类审查补充普通文档链接与事实核查。

路径规则按 add、modify、delete 匹配，优先级固定：deny > approval_required > generated > review_required。
先规范化真实仓库路径；拒绝绝对路径、父级逃逸、符号链接逃逸与无法解析路径，按真实大小写判定。
deny 立即拒绝并暂停；approval_required 需要对应范围的人类批准；generated 只允许指定 generator 修改；
review_required 允许实现但完成前需要独立审查。无匹配路径不能隐式获得批准。
真实 smoke 的秘密路径 pattern 与模板 allowlist 由版本化 [HARNESS_HOST.json](HARNESS_HOST.json) 提供；新增模板放行须
经宿主策略变更，不得在核心 runner 内写死项目特例。
控制平面规则本身属于 approval_required，防止自治执行降低自身边界。
新增依赖、越界或未批准的保护路径变更立即暂停；既有 Task 的明确授权对指定范围有效。

## 状态与证据

格式见 [STATE_SCHEMA.json](STATE_SCHEMA.json)，版本 1；时间戳是 UTC RFC 3339，以 Z 结尾。
runId 为 UUID；计划摘要与失败指纹为 SHA-256；Git 提交使用完整 40 位 SHA。
planPath、changedPaths、evidencePath 为仓库相对路径，worktreePath 为绝对路径。
gates 以 Gate ID 为键；每项保存 gateId、status、exitCode、startedAt、endedAt、evidencePath、headCommit。
Gate 状态为 pending、running、passed、failed、timed_out；passed 必须有退出码 0、起止时间和证据路径。
decisions 是 type、summary、createdAt 对象数组；摘要不得包含秘密。额外字段默认拒绝。
Schema 定义结构；合法状态转换、必需 Gate 集合与 HEAD/证据一致性由后续状态层执行验证。

正常路径：planned -> active -> verifying -> completed；验证失败进入 repairing 再回 verifying。
活动状态可以 paused；不可恢复的计划/权限/架构/外部依赖冲突进入 blocked。
禁止 active 直接 completed，失败 Gate 不能进入下一 Milestone。
每项必需 Gate 必须实际运行、退出成功、证据落盘并对应当前 HEAD，才能完成。
状态采用同目录临时文件、flush、fsync、原子替换；事件仅追加并带前一事件摘要。
运行目录 var/agent-harness/runs/<UUID>/ 包含 state.json、events.jsonl、diagnostics.md 和 evidence/，不提交。

## 修复预算与人工升级

- 同一失败指纹最多自动修复 3 次；单 Milestone 最多 5 个失败修复周期。
- 连续 2 轮 Git diff、失败指纹和 Gate 结果无实质变化，暂停并交人工。
- 指纹由退出码、失败测试名、首个稳定错误位置及规范化文本生成，排除时间戳、随机端口、临时路径和颜色。
- 基础设施失败先分类，不跳过/删除测试或放宽断言；认证失败、权限不足和缺少工具都安全暂停。
- Diagnostics 记录计划、Milestone、分支、HEAD、文件、失败 Gate/指纹、最小日志、修复记录、停止原因、
  所需人类决策和准确恢复命令。原始日志不得直接提交。

## 恢复与事故处理

恢复先重新 doctor；核对仓库、worktree、分支、HEAD、计划摘要和所有新改动归属。
HEAD 变化使相关 Gate 证据失效；不能沿用旧提交测试结果。
只有 Git 与事件链能证明的状态落后才可重建并记录 reconciled；计划变化、历史改写、分支切换、
未知代码变更或半写事件均暂停，不能静默覆盖。先保留本地证据，不自行重置或清理。
发现秘密泄露停止传播，报告存在性与安全保存位置，交人工处理凭据撤销，不能在报告中复制秘密。

## 无人值守与交付

Codex 仅在已验证独立 worktree 使用 --sandbox workspace-write、--approve-for-me、--strict-config、
--json、--output-schema；每轮重读磁盘状态。会话恢复仅限同一 Milestone 和有限预算。
禁止 --dangerously-bypass-approvals-and-sandbox、--dangerously-bypass-hook-trust 及等效选项。
CLI 缺失、未认证、能力不足、非零退出、超时或结构化响应损坏均暂停，不降级到无限权限执行器。
不向适配器传递环境秘密；Prompt 只含目标、权威路径、范围、脱敏失败摘要与剩余预算。

收尾提交前自审与独立审查按策略执行。Harness 不执行 force push、自动 rebase、自动合并、标签覆盖或生产部署。
普通 push/PR 只在项目授权范围；生产、认证、迁移和发布链须人工最终 Review。
仓库文档、GitHub 和 Obsidian 同步由任务授权与集成职责决定；受限任务将待同步内容明确交接。
脱敏器须覆盖 URL 凭据、Authorization、Cookie、JWT/常见 Token、密码、私钥块和敏感环境变量；测试只用伪秘密。
完成或暂停摘要遵守 [run-history 约定](run-history/README.md)，不复制原始模型事件与日志。
