# Token-Efficient Engineering Design

日期：2026-10-12

状态：已于 2026-10-12 获用户书面确认，待按实施计划执行

适用范围：Codex 全局协作规则、Survey 自治工程控制平面、本地质量门、GitHub Actions 和子 Agent 调度

## 1. 背景与实测基线

本项目从 2026-10-09 14:22 产品化方案启动到 2026-10-12 暂停开发期间，主 Agent 与项目相关子 Agent
产生约 14.87 亿原始 Token 吞吐。其中约 14.55 亿为缓存输入，缓存命中率约 98.1%；非缓存输入、模型输出和
推理合计约 3,298 万有效增量 Token。

同期形成约 47,840 行已提交新增内容和约 1,016 行未提交新增内容。测试执行本身不需要模型推理；主要 Token
浪费来自重复注入仓库上下文、成功日志、构建输出、相同失败堆栈、多个 Agent 重复诊断，以及本地与 GitHub
重复运行相同质量门。

当前每个新 Agent 若按入口顺序直接读取项目规则、永久记忆、自治 SOP、活动设计、活动计划和两份 Obsidian
总览，候选文本合计已超过 200 KiB；其中两份 Obsidian 总览约 138 KiB。产品化起点后至少创建了 91 个项目相关
子 Agent 会话。即使服务端缓存命中率很高，重复发现、重复选择和动态前缀仍会增加原始 Token、首轮延迟与上下文漂移。

当前实现已有以下基础：

- `gate_runner.py` 将完整 stdout/stderr 保存到 ignored 的运行目录。
- `diagnostics.py` 能脱敏、规范化错误并生成失败指纹。
- `GATE_MATRIX.yaml` 能根据变更路径选择 Gate 并取并集。
- Gate 证据绑定 Git HEAD，旧证据不会被误当成当前提交的通过结果。
- GitHub Actions 已覆盖前端、Java、网关、双数据库 E2E、生产发布和治理门禁。

仍需解决：

1. 诊断输入上限为 64 KiB，成功日志或重复堆栈仍可能进入模型上下文。
2. HEAD 任意变化会使所有相关 Gate 失效，无法安全复用未受影响的确定性测试结果。
3. 本地 Harness、多个子 Agent 和 GitHub Actions 没有统一的 Gate 证据键和并发租约。
4. Codex Prompt 没有可机器验证的上下文预算和按需扩展协议。
5. Token 使用数据存在于本机会话 JSONL，但没有脱敏、聚合、可比较的项目统计。

## 2. 目标

1. 测试、Lint、构建和容器运行默认不消耗模型 Token。
2. 成功 Gate 只向 Agent 暴露固定字段摘要，不暴露完整 stdout/stderr。
3. 失败 Gate 默认只暴露一个根因、最多三个关联错误和不超过 4 KiB 的脱敏诊断。
4. 相同证据键的 Gate 在本地、子 Agent 和 GitHub 之间只执行一次或明确复用一次。
5. 安全地复用与当前变更无关的确定性 Gate，同时保持生产、安全、迁移和发布 Gate 的完整 HEAD 绑定。
6. 每个 Codex 循环有明确的上下文预算、升级条件和停止条件。
7. 建立不包含 Prompt、源代码、秘密或原始日志的 Token 使用统计，按 Milestone 比较投入产出。
8. 保持现有 TDD、测试强度、双库验证和生产验收标准，不以减少 Token 为由减少必要覆盖。
9. 使用 Obsidian 与本地记忆维护连续开发所需的稳定事实，但新 Agent 默认只加载可校验的紧凑任务胶囊。
10. 让所有同类 Agent 使用字节级稳定的共享前缀，最大化 Prompt 缓存并避免每个 Agent 重新发现项目入口。

## 3. 非目标

- 不删除测试、不降低断言、不用 skip 或 mock 冒充真实验收。
- 不把完整日志、会话 JSONL 或 Token 明细上传到 GitHub。
- 不根据代码行数自动判断质量或完成度。
- 不允许 Agent 自行把重型、安全或生产 Gate 降级为轻量 Gate。
- 不用模型总结成功日志；成功摘要必须由确定性程序生成。
- 不以缓存证据替代最终生产候选版、备份恢复、升级回滚或四平台原生验收。
- 不把 Obsidian 当作代码、测试、Git、Harness 状态或机器能力的权威来源。
- 不要求每个 Agent 直接读取或搜索完整 Obsidian vault。

## 4. 总体架构

```text
变更路径
  -> Gate Planner
     -> 本地快速 Gate
     -> 本地/Docker 集成 Gate
     -> GitHub 完整/生产 Gate
        -> 原始证据（不进入模型）
        -> 结构化摘要（默认上下文）
        -> 失败诊断包（按需上下文）
           -> Codex 单一修复 Agent
```

控制面新增六个协作组件：

- `EvidenceKey`：确定相同 Gate、相同输入和相同环境是否可复用。
- `GateLease`：阻止并行 Agent 重复运行相同 Gate。
- `GateSummary`：成功与失败的固定字段机器摘要。
- `ContextManifest`：记录本轮允许进入 Codex Prompt 的文件、摘要、字节数和原因。
- `ContextCapsule`：由权威文件和已验证本地记忆确定性生成的只读、内容寻址任务胶囊。
- `MemoryProjection`：把仓库与 Harness 的已验证进度投影到 Obsidian，并检测人工内容与机器块的漂移。

## 5. Token 统计口径

### 5.1 三种指标

- `raw_tokens`：模型服务记录的输入、输出与推理总量，包含缓存输入。
- `cached_input_tokens`：重复上下文命中的缓存输入，仅用于观察上下文膨胀。
- `active_tokens`：`input - cached_input + output + reasoning_output`，作为工程效率主要指标。

不得把原始 Token 直接等同于成本或有效工作量。Codex 账户配额只返回百分比时，报告不得反推出不存在的绝对上限。

### 5.2 本地聚合器

新增只读聚合命令，从本机 Codex 会话记录中读取累计计数，按会话和时间窗口去重：

```text
agent-harness token-report --since <RFC3339> --plan <path> [--json]
```

输出仅包含：时间窗口、主/子 Agent 数量、原始 Token、缓存输入、有效增量 Token、Gate 次数、失败指纹数量、
复用次数和 Milestone 标识。禁止输出 Prompt、消息正文、文件内容、账户 ID 或秘密。

统计结果默认保存到 ignored 的运行目录。提交到 `docs/agent/run-history/` 时只允许写整数聚合与计算口径。

### 5.3 效率指标

每个 Milestone 记录：

- `active_tokens_per_completed_task`
- `active_tokens_per_unique_failure`
- `raw_to_active_ratio`
- `gate_execution_count`
- `gate_reuse_count`
- `duplicate_gate_avoided_count`
- `diagnostic_bytes_exposed`

代码行数仅作趋势参考，不作为验收标准。

## 6. 上下文准入与预算

### 6.1 默认准入

Codex 每个修复循环只接收：

1. 当前 Milestone 标题、验收标准和允许路径。
2. 相关设计与计划的精确章节引用，不复制整份文档。
3. 变更文件清单和有限 diff 统计。
4. 当前失败的结构化摘要与失败指纹。
5. 剩余修复次数、Token 预算和停止条件。

默认禁止注入：

- 成功测试日志。
- 依赖下载和构建进度。
- 完整 Git 历史、完整计划、完整仓库树。
- 已处理过且指纹未变化的失败日志。
- 其他 Agent 的完整对话或原始工具输出。

### 6.2 预算层级

每轮 Context Manifest 使用以下上限：

- `summary`: 4 KiB，默认失败摘要。
- `focused`: 16 KiB，包含一个失败测试的必要堆栈与相关源码片段。
- `expanded`: 48 KiB，仅在 `focused` 不能定位且记录升级原因时允许。

超过 `expanded` 不继续扩大 Prompt。应改为缩小测试、建立最小复现或暂停并报告诊断索引。

### 6.3 子 Agent

- 子 Agent 只获得其拥有路径、接口、测试命令和验收标准。
- 父 Agent 不转发完整历史，只转发结构化任务包。
- 相同失败指纹只分配给一个修复 Agent。
- Review Agent 读取 diff、契约和摘要，不重新运行已通过 Gate。
- 子 Agent 返回固定 JSON 结果和简短人类摘要，不返回完整日志。

### 6.4 Agent 启动胶囊

父 Agent 或 Harness 在创建子 Agent 前生成一个不可变 `ContextCapsule`。子 Agent 不自行遍历仓库入口，也不直接读取完整
设计、计划、历史报告或 Obsidian 长文档。胶囊按固定顺序包含：

1. 全局硬约束摘要和版本摘要。
2. 项目身份、权威入口和不可变安全边界。
3. 当前 Milestone 的精确验收条件。
4. Agent 拥有路径、禁止路径、输入接口和输出格式。
5. 当前 Git/工作区摘要、相关 Gate 状态和唯一失败诊断。
6. 需要时引用的源文件路径与行号，不复制无关正文。

大小预算：

- 共享稳定前缀不超过 12 KiB。
- 单 Agent 任务增量不超过 8 KiB。
- 默认失败诊断不超过 4 KiB。
- 新 Agent 首轮总胶囊目标不超过 24 KiB。

稳定前缀不得包含当前时间、随机 ID、临时路径、端口或动态进度；这些字段统一放在末尾任务增量，避免微小变化破坏 Prompt
缓存。胶囊使用规范 JSON 和 SHA-256，存放在 ignored 的 `var/agent-harness/context-cache/`。Agent 返回结果时必须带
`capsuleDigest`；摘要未变化时不得重新读取上游长文档。

当任务所需事实不在胶囊中时，Agent 只能按 `ContextManifest` 申请一个精确章节或源文件片段。每次扩展记录路径、范围、
字节数和原因，禁止以“了解项目”为由加载完整蓝图或历史报告。

## 7. 测试执行分层

### 7.1 本地快速层

每次有意义的代码增量运行受影响的定向单测、Lint、类型检查和静态检查。Harness 直接执行，不调用模型参与测试过程。

### 7.2 本地集成层

在单个 Task 收口时运行相关 Docker、数据库、PHPUnit、Java 或 Playwright 集成 Gate。使用稳定测试数据库和依赖缓存，
完整日志只落本地证据目录。

### 7.3 GitHub 完整层

形成稳定提交后运行完整回归、双数据库矩阵、生产 Compose、Ubuntu 原生安装和 Release Gate。Codex 只读取 Check 状态、
结构化 Job Summary 和 Artifact 索引；失败时由确定性提取器生成诊断包。

本地已通过不能跳过必须在 GitHub 或目标 Ubuntu 环境验证的 Gate；GitHub 已通过也不能替代尚未覆盖的本地真实设备验收。

## 8. 结构化摘要

每次 Gate 必须原子生成 `summary.json`，至少包含：

```json
{
  "schemaVersion": 1,
  "gateId": "platform-java-tests",
  "status": "passed",
  "evidenceKey": "sha256:...",
  "headCommit": "...",
  "durationSeconds": 74,
  "testCount": 1478,
  "failureCount": 0,
  "failureFingerprint": null,
  "diagnosticPath": null,
  "rawEvidencePath": "...",
  "reused": false
}
```

成功摘要不得包含 stdout/stderr。失败摘要可增加测试名、文件、行号、失败类型、期望/实际值和最多三个关联错误，脱敏后
不超过 4 KiB。完整证据路径只能指向当前工作区 ignored 目录或受控 GitHub Artifact。

## 9. 证据键与安全复用

`EvidenceKey` 由以下规范化字段计算 SHA-256：

- Gate ID 和 Gate 定义摘要。
- 相关源文件、测试文件、配置文件和生成器输入的 Git blob 摘要。
- 依赖 lockfile、Dockerfile、Compose 和数据库迁移摘要。
- 操作系统、架构、数据库类型、工具链主版本和必要环境标签。
- 受保护路径策略与 Harness Schema 版本。

允许内容寻址复用：确定性单元测试、Lint、类型检查、静态生成一致性和文档检查。

始终绑定完整 HEAD、不得跨提交复用：

- 认证、授权、租户隔离和安全门禁。
- 数据库迁移、备份恢复、升级回滚。
- 生产 Compose、Release、SBOM、签名和原生平台验收。
- 真实浏览器纵向旅程及测试数据隔离。
- 人工审批要求的 Gate。

任何输入无法完整建模时必须 fail closed，重新执行 Gate。

## 10. Gate 租约与并发去重

本地租约位于 ignored 的运行目录，键为 `EvidenceKey`。创建使用原子文件操作，记录持有者、开始时间、进程标识和超时。
租约超时不能自动宣告测试失败或通过；必须先验证持有进程和证据完整性。

多个 Agent 请求同一 Gate 时：

1. 第一个请求持有租约并执行。
2. 其他请求进入等待，不启动重复进程或容器。
3. 完成后所有等待者读取同一个结构化摘要。
4. 失败只创建一个修复任务。

GitHub 使用 `concurrency` 和提交/Gate 标识避免同一提交重复 Workflow；不能取消已经作为发布证据使用的生产验收。

## 11. Codex 自愈协议

1. Gate 失败后先由本地确定性提取器生成 4 KiB 以内诊断。
2. Harness 用失败指纹检查是否为已知重复。
3. 首次失败向 Codex 提供 `summary`；无法定位时才升级 `focused`。
4. 修改后只运行受影响的最小 Gate；Task 收口时再运行该 Task 完整 Gate。
5. 相同失败指纹连续两次且 diff 摘要无实质变化时暂停，不再消耗第三轮诊断 Token。
6. 架构冲突、测试基础设施故障或外部服务失败不得交给 Codex 盲目改业务代码。
7. 完成前仍须执行计划要求的完整门禁，Token 优化不能改变交付标准。

## 12. GitHub 自动化

GitHub Workflow 需要：

- 为每个 Job 写固定格式的 `$GITHUB_STEP_SUMMARY`。
- 失败时上传脱敏 `summary.json` 和必要的有限诊断 Artifact。
- 成功时不上传控制台日志副本；GitHub 自身日志按既有保留策略保存。
- 使用依赖缓存和 Docker BuildKit 缓存，但缓存键必须包含 lockfile 或构建输入摘要。
- 重型矩阵只在路径过滤命中、稳定提交或手工发布候选触发时运行。
- PR 修复提交可以取消未成为证据的旧提交 CI；发布候选和生产证据不得取消。
- Harness 可导入 GitHub Check 结果，但必须验证仓库、workflow、commit SHA、job 名称和 conclusion。

## 13. Obsidian 与本地记忆

### 13.1 事实分层

事实优先级固定为：

1. 当前 Git、代码、测试和 Harness 机器状态。
2. 仓库内批准设计、实施计划、`PROJECT_MEMORY.md` 和机器能力数据。
3. Codex 全局长期记忆中的通用方法与仓库映射。
4. Obsidian 中的人类可读项目总览、决策背景和进度投影。
5. 历史会话和旧报告。

低优先级资料不得提升高优先级状态。Obsidian 记录“已完成”时必须携带分支、提交、测试证据或 Harness run；缺少这些字段
只能视为叙述，不能进入任务胶囊的已完成项。

### 13.2 Survey 同步入口

Survey 当前人类进度入口为：

- `LimeSurvey本土化方案/04-开发方案总蓝图（仓库校准版）.md`
- `LimeSurvey本土化方案/08-需求进度与方案修订（仓库实证）.md`

实际 vault 根路径属于本机配置，不写入全局规则或提交到仓库。Harness 通过 ignored 的宿主配置定位这些相对路径。

04 号文件保存里程碑级蓝图与近期交付摘要；08 号文件保存需求验收口径、缺口和路线调整。两份文件不再被每个 Agent
全文读取。Harness 只解析受控机器块或由人工确认的固定标题，并生成紧凑投影。

### 13.3 事件驱动同步

Obsidian 不按每轮对话更新，只在以下事件同步：

- 计划或设计获批。
- Milestone 完成、暂停或阻塞。
- 形成可追溯提交、PR、Release 或生产验收证据。
- 验收结论、优先级或下一步发生实质变化。

同步内容固定为日期、分支、提交/PR、Harness run、测试摘要、完成项、遗留、下一步和来源摘要。相同来源摘要再次同步必须
幂等。人工正文与机器块分离，Harness 只能替换带版本和摘要的机器块，不得重写整篇 Obsidian 文档。

### 13.4 本地连续记忆

连续开发使用四层记忆：

- 全局层：跨项目通用协作规则和 Token 优化方法。
- 项目层：仓库身份、技术栈、事实入口和稳定安全边界。
- Milestone 层：批准设计、Plan、进度、Gate 和未决决策。
- Task 层：单 Agent 拥有范围、失败指纹、当前 diff 和输出契约。

Harness 为每层保存内容摘要。只有上游摘要变化时才重建下游胶囊。新 Agent 读取胶囊和任务增量，不重复执行项目发现。
父 Agent 收到子 Agent 结果后只合并结构化结果，不把子 Agent 对话回灌到其他 Agent。

### 13.5 漂移与冲突

- Obsidian 指向不存在的提交、错误分支或未通过 Gate 时标记 `stale`，不自动改仓库事实。
- 仓库进度领先于 Obsidian 时生成待同步差异，不阻塞编码，但阻塞最终交付闭环。
- 人工修改机器块摘要时拒绝覆盖并请求复核。
- Obsidian 不可用时继续本地开发，保留待同步事务；不得让知识库故障导致重复读取完整历史。

## 14. 全局与项目规则落点

设计批准并实施后，信息分层保存：

### Codex 全局

- `~/.codex/AGENTS.md`：只增加简洁硬规则，包括成功日志不进上下文、失败诊断最小化、先本地/CI 执行再由 Agent 诊断、
  相同失败和相同 Gate 去重、不得为省 Token 降低测试标准。
- `~/.codex/memories/engineering-token-efficiency.md`：保存通用统计口径、上下文预算、日志准入和跨项目复用方法。
- `~/.codex/memories/project-context-index.md`：保存项目到权威入口、Obsidian 相对入口和胶囊生成配置的索引；不保存动态进度。

全局文件不得记录 Survey 的绝对路径、当前 Token 数或项目专属 Gate。

### Survey 项目

- `AGENTS.md`：增加项目级 Token 与测试上下文硬约束。
- `docs/agent/TOKEN_EFFICIENCY.md`：保存本设计的操作手册、指标与排障方式。
- `GATE_MATRIX.yaml`：声明 Gate 层级、摘要器、复用类别和 CI 要求。
- `STATE_SCHEMA.json`：增加证据键、摘要路径、复用状态、诊断字节数和执行来源。
- Harness：实现统计、摘要、复用、租约、上下文清单、任务胶囊和 Obsidian 幂等同步。
- GitHub Workflows：实现结构化摘要、路径过滤、并发去重和 Artifact 边界。

## 15. 安全与隐私

- Token 聚合器只读取数值字段与必要的时间、线程来源元数据，不解析或输出消息正文。
- 诊断摘要继续使用现有秘密脱敏器；不能把脱敏失败当作可接受降级。
- 原始日志、Prompt、会话 JSONL 和未脱敏截图不得提交、上传或写入 Obsidian。
- GitHub 证据导入必须防止来自 fork、错误 SHA 或同名恶意 Workflow 的伪造结果。
- 内容寻址缓存不能跨仓库、跨租户或跨不兼容工具链共享。
- 全局记忆只保存方法和硬约束，不保存账户用量、账户 ID 或项目秘密。
- Obsidian 的长篇正文、附件、嵌入内容和未受控链接不得自动进入 Agent 胶囊。
- 胶囊只接受允许的本地权威文件和固定标题，所有来源保存摘要，防止被未受控笔记内容扩展权限。

## 16. 迁移策略

1. 先增加 Schema、摘要模型和兼容读取，旧运行记录保持可读。
2. 增加成功摘要和 4 KiB 失败诊断，不立即启用证据复用。
3. 增加 Gate 租约，消除同机并发重复。
4. 为确定性轻量 Gate 启用内容寻址复用并运行故障注入测试。
5. 接入 GitHub 摘要和受控 Check 导入。
6. 建立 Obsidian 受控机器块、同步事务和漂移检查。
7. 启用 Context Capsule、Context Manifest 和 Token 报告；观察两个 Milestone 后再调整阈值。

任何阶段发现摘要遗漏根因、证据误复用或秘密泄漏，立即关闭复用并回到完整执行；不得关闭原始证据保留。

## 17. 验收标准

- 成功测试日志进入 Codex Prompt 的字节数为 0。
- 默认失败诊断不超过 4 KiB，升级诊断有明确原因且不超过 16 KiB。
- 相同 `EvidenceKey` 的并发请求只产生一个测试进程和一份原始证据。
- 确定性轻量 Gate 可在无关 HEAD 变化后安全复用；受保护 Gate 永不跨 HEAD 复用。
- 同一失败指纹不会重复注入相同诊断。
- 本地和 GitHub 摘要能验证来源、HEAD、Gate 定义和证据摘要。
- Token 报告不会输出消息正文、源代码、秘密、账户 ID 或原始日志。
- 故障注入覆盖伪造缓存、陈旧租约、截断摘要、错误 SHA、恶意 Artifact、脱敏失败和并发竞争。
- 现有 Harness、前端、Java、Python、PHP、双库 E2E、生产和 Release Gate 不因本优化被删除或降低。
- 连续两个代表性 Milestone 的测试相关有效 Token 比当前基线下降至少 60%，总有效 Token 下降目标为 35%–45%。
- 新 Agent 默认首轮胶囊不超过 24 KiB，不再全文读取约 200 KiB 的项目入口与 Obsidian 总览。
- 十个并行 Agent 使用同一字节级稳定前缀和各自任务增量；共享前缀摘要一致且不含动态字段。
- 相同胶囊摘要不会触发重复项目发现；每次上下文扩展都能追踪到路径、范围、字节数和原因。
- Obsidian 同步幂等，人工正文不被改写；错误提交或陈旧进度不能提升 Harness 或能力状态。
- 两个代表性 Milestone 中，子 Agent 启动有效 Token 相比当前基线下降至少 70%，原始上下文吞吐下降目标为 80%。

## 18. 预期效果

在不降低测试覆盖和生产验收标准的前提下，预计将后续有效增量 Token 从 5,600 万–9,800 万降低到约
3,200 万–5,800 万。收益主要来自成功日志零注入、失败摘要缩减、同 Gate 去重、选择性证据复用、Obsidian 事件驱动投影、
共享稳定前缀和子 Agent 任务胶囊。

Obsidian 和本地记忆不会让独立模型会话“免费共享思考”；它们通过减少重复发现、缩短输入、稳定 Prompt 前缀并提高缓存
复用来降低消耗。最终目标是在连续开发质量不变的前提下，将子 Agent 启动上下文从约 200 KiB 候选资料降至不超过
24 KiB，减少约 88% 的启动文本，并把后续总有效 Token 目标再降低约 35%–45%。

该估计是优化目标，不是完成声明。实际效果以 Harness 聚合的两个完整 Milestone 数据为准。
