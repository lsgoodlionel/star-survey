<!-- harness-delivery-status: local_validated_sync_pending -->
# 自治工程 Harness

这是一个 Python 3.11+、仅依赖标准库的确定性控制套件。它把批准计划、Git 身份、路径策略、质量门、
重试预算、脱敏诊断和受控 Codex 执行组合成可恢复的单 Milestone 状态机。它不是项目管理平台，也不会
自动合并、发布或部署。

## 核心套件

可独立提取复用的代码位于 `tools/agent-harness/agent_harness/`、`tools/agent-harness/drills/` 和
`scripts/agent-harness`：

- CLI：`doctor/init/status/next/gate/record-decision/record-review/pause/resume/run-codex/finalize`，使用稳定退出码。
- 状态与恢复：原子 `state.json`、摘要链接的 `events.jsonl`、Git 分支/HEAD/工作区漂移检测。
- 策略与 Gate：按实际改动取 Gate 并集，禁止计划删减自动 Gate；路径逃逸、符号链接和未知路径均失败关闭。
- 终止条件：同一失败指纹第 3 次、总失败周期第 5 次、连续第 2 轮无进展时暂停。
- 诊断：原始输出只写 ignored runtime，提交摘要对 URL 凭据、Header、Token、JWT、私钥和命名秘密脱敏。
- Codex：只在独立 worktree 中使用固定 `workspace-write` 参数数组；不接受权限绕过参数或任意执行器。

安装到另一个仓库时，复制上述三个入口，保留目录相对关系，并提供下一节的版本化策略文件。目标主机必须有
Python 3.11-3.14 和 Git；Node、Java、Docker、Codex 是否必需由宿主 Gate 与实际 Milestone 决定。

仓库根常用命令：

```sh
scripts/agent-harness doctor --json
scripts/agent-harness init --plan docs/superpowers/plans/approved-plan.md --milestone task-1
scripts/agent-harness status --run-id <run-id> --json
scripts/agent-harness next --run-id <run-id> --json
scripts/agent-harness gate --run-id <run-id>
scripts/agent-harness record-decision --run-id <run-id> --type <type> --summary <summary>
scripts/agent-harness pause --run-id <run-id> --reason <sanitized-reason>
scripts/agent-harness resume --run-id <run-id>
scripts/agent-harness run-codex --run-id <run-id> --max-cycles 1
scripts/agent-harness finalize --run-id <run-id>
```

恢复前先运行 `doctor`，再运行 `status` 与 `next`；`resume` 会重新核对计划摘要、策略绑定、worktree、分支、
HEAD、工作区和工具环境。审批路径、真实秘密、依赖新增、生产/发布、认证或迁移等情况必须暂停，由宿主人工
流程处理；不能用 `record-decision` 绕过路径策略。

## 项目策略模板

每个宿主仓库负责维护以下版本化输入；它们是模板边界，不属于核心套件的通用业务事实：

- `AGENTS.md`：稳定强制规则与详细文档入口。
- `docs/agent/AUTONOMY.md`：准入、恢复、升级和发布边界。
- `docs/agent/GATE_MATRIX.yaml`：路径 profile 到固定参数数组 Gate 的映射。
- `docs/agent/PROTECTED_PATHS.yaml`：`deny/approval_required/review_required/generated` 路径策略。
- `docs/agent/STATE_SCHEMA.json`：持久化 wire contract。
- `docs/agent/PROJECT_MEMORY.md`：宿主项目稳定事实，不保存临时运行状态。
- `docs/agent/HARNESS_HOST.json`：active plan、secret policy、fixture scope、宿主文档与交付 manifest 路径。
- `docs/agent/HARNESS_DELIVERY.json`：计划 checkbox、ledger、implementation commit、验证证据和外部同步状态的唯一清单。
- `docs/superpowers/plans/`：已经人工确认、包含唯一 Milestone 与明确文件范围的计划。

`GATE_MATRIX.yaml` 与 `PROTECTED_PATHS.yaml` 使用 JSON 语法（YAML 1.2 子集），无需 PyYAML。命令只能是非空
字符串数组；加载器拒绝未知字段、重复 key/ID、无效引用、非正整数 timeout、绝对路径、Windows 路径与 `..`。
原始运行目录必须是被 Git 忽略且未跟踪的 `var/agent-harness/runs/<UUID>/`。

## 宿主集成

`.github/workflows/agent-governance.yml` 是当前宿主的只读 CI 接线：固定 action commit，运行配置测试、完整
Harness 套件、fake drills、危险参数扫描和计划/记忆漂移检查。它不持有写权限，也不执行合并、制品发布或部署。
unit job 使用确定性 fake tools；Linux integration job 通过 `run_tests.py --ci` 显式传入固定镜像 digest 与受信 Docker
绝对路径，并以 `--fail-on-skip` 强制零 skip。环境变量不能替代这些受控参数。

manifest 在独立复审 pending 时必须保留空 `reviewEvidence.reports`。只有交付控制器取得 security、DX/CI、whole-branch
三份已跟踪报告后才能标记 complete；每份报告都必须绑定 implementation commit/range、内容 SHA-256，并同时包含
`SPEC_COMPLIANCE=APPROVED` 与 `CODE_QUALITY=APPROVED`。本地实现者不能自行填充这些证据。

workflow 的 forbidden-option scanner 只支持 block-style `run:`、`"run":`、`'run':`，以及 inline scalar 或
`|`、`>`、`|-`、`>-` block scalar。flow mapping、转义 key、显式 tag、anchor/alias 和其他 scalar indicator 在
`run` 上均失败关闭；未被该子集解析但含 Codex-like `exec` 的表示同样拒绝。Codex 必须是裸 executable，不能由
`command`、`env`、绝对路径 wrapper 或其他命令包装；无执行语义的 `echo "codex"` 文本仍允许。

review report 的批准证据只接受文件 EOF 的 canonical 两行 block，顺序固定为 `SPEC_COMPLIANCE=APPROVED`、
`CODE_QUALITY=APPROVED`，每个 key 在全文只能出现一次且必须与 manifest 一致。历史段落、引用、code block、重复或
后续 `CHANGES_REQUIRED` 均不能提升 `independentReview`。

宿主可把自己的 Node、Java、Docker、双数据库、浏览器或产品化检查登记进 Gate 矩阵；核心套件不会猜测这些
命令。GitHub 分支同步、PR、Obsidian 和生产审批同样属于宿主交付流程，不由 Harness 自动完成。

默认 drill 全部使用临时目录和 fake 命令：

```sh
scripts/agent-harness --python tools/agent-harness/drills/run_drills.py
scripts/agent-harness --python tools/agent-harness/drills/run_drills.py --check-forbidden-options
scripts/agent-harness --python tools/agent-harness/drills/run_drills.py --check-docs
```

真实 Codex smoke 只能人工显式运行一次，并且只操作由当前 HEAD 创建的 disposable worktree：

```sh
scripts/agent-harness --python tools/agent-harness/drills/run_drills.py --allow-real-codex
```

runner 在调用 Codex 前按宿主配置检查 tracked、untracked 和 ignored inventory，拒绝秘密路径、目录 symlink 与解析逃逸；
plan/fixture 采用拒绝 symlink 的防跟随原子创建。它固定一个 cycle，只允许修改 fixture，并验证临时 worktree、分支和
路径均已清理。`completed` 还要求 fixture 精确语义、非空 required Gates 全部以当前 HEAD 的退出码 0 证据通过；登录、
配额、CLI 或受控环境失败必须得到脱敏 `paused` 证据。runner 自身或清理不变量失败返回 `failed`，不能声称清理完成。
CI 永不使用 `--allow-real-codex`。

本地裸 `python3` 若低于 3.11 会按设计失败；Harness 测试应使用版本化入口选择受控 runtime：

```sh
scripts/agent-harness --python -m unittest discover -s tools/agent-harness/tests -p 'test_*.py' -v
```

当前限制：没有常驻队列或 Web 控制台；不自动批准 protected path、依赖、review、push、merge 或生产动作；
真实工具可用性仍取决于宿主登录、配额和安装状态；临时工作区删除后只保留调用方主动记录的脱敏摘要。
