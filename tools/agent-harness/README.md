# Agent Harness 配置契约

本 Task 仅交付配置模型/解析、策略文档和状态 Schema。CLI、状态持久化、路径匹配、Gate runner、脱敏和 Codex 适配尚未交付。

仓库根验证：

```sh
python3 -m unittest discover -s tools/agent-harness/tests -p 'test_config.py' -v
python3 -m unittest discover -s tools/agent-harness/tests -p 'test_*.py' -v
git check-ignore -v var/agent-harness/runs/example/state.json
```

目标 Python 3.11+，实现只用标准库。加载模块时将 tools/agent-harness 放入 Python 模块搜索路径。
权威规则：[AGENTS.md](../../AGENTS.md)、[SOP](../../docs/agent/AUTONOMY.md)、[永久记忆](../../docs/agent/PROJECT_MEMORY.md)。

## 公共接口

config.py 提供 ConfigError、不可变 dataclass GateDefinition、GateMatrix、PathRule、PolicyConfig，及：

```python
load_gate_matrix(path: Path) -> GateMatrix
load_protected_paths(path: Path) -> PolicyConfig
```

GateDefinition 字段为 id、command（tuple）、cwd（仓库相对路径）、timeout_seconds（正整数）。
GateMatrix 包含 version、gates（GateDefinition tuple）、profiles（profile 名到 Gate ID tuple 的只读映射）、
paths（profile 名到路径 pattern tuple 的只读映射）。后续 runner 按 paths 选 profile 并对 Gate ID 取并集。
PathRule 包含 id、patterns、operations、action 和可选 generator（参数 tuple）。PolicyConfig 包含 version、rules tuple。

## 磁盘格式

[GATE_MATRIX.yaml](../../docs/agent/GATE_MATRIX.yaml) 和 [PROTECTED_PATHS.yaml](../../docs/agent/PROTECTED_PATHS.yaml)
使用 JSON 语法，即 YAML 1.2 子集，由 json 解析，不接受一般 YAML。version 必须为整数 1。

Gate 文件只有 version、gates、profiles 三个顶层字段。每个 Gate 必须有 id、command、cwd、timeout_seconds；
每个 profile 必须有 paths 和 gates 数组。引用必须存在且无重复。
策略文件只有 version、rules；每条规则必须有 id、patterns、operations、action，generated 还必须有 generator。
operations 只接受 add、modify、delete；action 只接受 deny、approval_required、review_required、generated。
generator 仅用于 generated。命令必须是非空字符串数组，不接受 Shell 字符串。

加载器拒绝未知/缺失字段、重复 JSON key/ID、空集合、非法引用、无效类型（包括将 bool 当整数）、非有限 JSON 数字、
空路径与 NUL、绝对路径、Windows 路径和含 .. 的路径。相对 cwd 允许 .。
所有错误显式抛 ConfigError；加载器只读取与验证，不执行命令或判断批准是否存在。
符号链接真实路径、匹配大小写、操作判定与规则优先级由后续 Git guard 实施；优先级见 SOP。

[STATE_SCHEMA.json](../../docs/agent/STATE_SCHEMA.json) 使用 JSON Schema draft 2020-12，版本 1，
仅定义机器状态结构。运行转换、摘要校验、证据有效性和预算由后续状态/执行层检查。
原始证据只写 var/agent-harness/；[run-history](../../docs/agent/run-history/README.md) 定义脱敏历史格式。
