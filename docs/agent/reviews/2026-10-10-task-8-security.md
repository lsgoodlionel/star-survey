# Task 8 最终独立安全边界复核

审查角色：安全边界审查
审查方式：只读静态审查与确定性测试复核；未修改 tracked 文件，未提交、未推送，未运行真实 Codex。

Reviewed range: `c7b9366142ab6f8b0570d208c156fc0392c007f1..79e85b7208d9647781e978e2dff90158d28f2f0a`
Reviewed commit: `79e85b7208d9647781e978e2dff90158d28f2f0a`

## 结论

未发现可报告的安全漏洞、沙盒边界绕过、真实 Codex 意外执行路径、工具身份替换路径或交付证据伪造路径。变更符合已确认的自治控制平面规范，代码质量满足本轮最终复核要求。

当前 tracked `docs/agent/HARNESS_DELIVERY.json` 仍以 `locally_reviewed_sync_pending` 绑定较早审查提交，这是本次最终报告尚未由交付控制器回填前的预期状态，不代表 `79e85b72` 已完成 GitHub/Obsidian 最终同步。本报告可作为后续更新最终三路审查证据链的安全审查输入。

## 重点边界

### Hosted Python 信任链

- 两个 GitHub Actions job 均使用固定 SHA 的 `actions/checkout` 与 `actions/setup-python`，并在 wrapper 执行前对 `/opt/hostedtoolcache/Python`、版本目录、`bin` 目录和最终 `python3.11` 文件执行 `chmod go-w`。
- `pythonLocation` 仅接受 `/opt/hostedtoolcache/Python/3.11.<数字>/x64`；空格、Tab、换行及伪造 suffix 不能拆分为额外路径。
- shell 层验证固定根、父目录所有者与写权限、符号链接深度/环路、设备号、inode、uid 和 mode；Python 隔离层再次验证同一链、最终 `sys.executable`、版本化文件名及 3.11-3.14 范围。
- hosted 入口不满足约束时 fail closed 或回退到受信任的系统候选，不接受 PATH 中的仓库可写程序。

### Fake Codex CI 隔离

- 普通治理 workflow 未传入 `--allow-real-codex`，默认 fault drill 仅调用 `run_fake_drills()`。
- 自治循环测试显式 patch `build_codex_command` 与 `run_codex`；adapter 测试使用临时 fixture executable 或受控 subprocess stub，不调用真实 Codex。
- 真实 smoke 只能由显式 `--allow-real-codex` 进入，并位于独立 disposable worktree 流程；本次复核没有调用该入口。
- workflow 权限仅为 `contents: read`，不存在 push、merge、release、deploy 或写权限配置。

### 工具身份指纹

- 受控工具绑定固定候选根、入口与最终目标，身份字段包含 `st_dev`、`st_ino`、`st_uid`、`st_mode`、`st_size`、`st_ctime_ns`，并保存完整符号链接链。
- 执行前重新校验入口、目标、父目录、根策略、身份字段和符号链接链；同长度篡改、ctime 变化、可写目标、临时目录或仓库目录中的工具均被拒绝。
- Codex argv 固定为 `workspace-write`、`--approve-for-me`、`--strict-config`、JSON schema 与固定 worktree；环境按 allowlist 重建，原始日志被限制在 ignored run 目录。

### 完整历史与交付证据

- 两个 CI checkout 均设置 `fetch-depth: 0`，为 `merge-base --is-ancestor` 与 review range 验证提供完整历史。
- resume 拒绝 shallow repository、历史回退、非线性提交、缺少唯一 `Agent-Run-Id` trailer 的后继提交及超出批准范围的 touched paths。
- delivery 校验绑定 implementation commit 与 evidence HEAD，要求三名固定 reviewer、三个不同 tracked 非 symlink 报告、不同 SHA-256、range 终点等于 implementation commit、起点为其祖先，并且报告 EOF canonical verdict 与 manifest 一致且双 APPROVED。
- Gate/finalize 重新绑定当前 HEAD、工作区摘要、命令、cwd、timeout、exit code 和原始证据位置；HEAD 或内容变化会使旧证据失效。

## 验证证据

- 用户提供的 GitHub Actions run `38064236869`：全部通过；描述为 unit 401、确定性故障演练、docs/forbidden checks，以及 Linux integration 8/8 zero skip。
- 本地非 Linux 分区：`scripts/agent-harness --python tools/agent-harness/run_tests.py --exclude-substring linux_wrapper --fail-on-skip`，402 tests，全部通过，0 skip，379.603s。
- 本地治理测试：15 tests，全部通过。
- 本地 `--check-forbidden-options`：`PASS forbidden-option-policy`。
- 本地 `--check-docs`：`PASS plan-memory-drift`。
- `git diff --check c7b93661..79e85b72`：通过。
- 静态计数为 410 个 test 方法；本地分区为 402 + Linux 8。用户提供的 hosted unit 401 与当前静态计数相差 1，属于待交付控制器按 GitHub 原始 run 明细校准的证据摘要口径，不构成代码或安全发现。
- 本地 Linux integration 复跑因当前沙盒无 Docker socket 权限而返回 8 个 `linux-fixture:permission-denied`，没有进入断言执行；Linux 通过结论使用用户提供的 hosted 8/8 zero-skip 证据，不把本地环境阻断记为产品失败。
- Codex Security diff scan `244dc355-187d-44ea-9b95-f0cc367cdf9a`：14 个 source review item 全覆盖，4 个重点安全面均为 `no_issue_found`，0 findings，coverage complete。

## 发现

无可报告发现。

剩余交付动作不属于本只读审查：由交付控制器将最终三路报告复制到 tracked canonical review 路径，更新 manifest 的 reviewed commit/range/digest，依据 GitHub 原始 run 校准测试计数，并继续完成 GitHub 与 Obsidian 状态同步。

SPEC_COMPLIANCE=APPROVED
CODE_QUALITY=APPROVED
