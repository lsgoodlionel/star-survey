# 自治工程控制平面最终全分支复核

日期：2026-10-10
角色：全分支规格一致性与代码质量审查
审查方式：只读；未修改 tracked 文件，未提交，未推送，未运行真实 Codex

## 审查绑定

- Reviewed range: `c7b9366142ab6f8b0570d208c156fc0392c007f1..79e85b7208d9647781e978e2dff90158d28f2f0a`
- Reviewed commit: `79e85b7208d9647781e978e2dff90158d28f2f0a`
- Branch: `feat/admin-product-alignment`
- 审查开始与结束时，`git status --short` 均为空；HEAD 与 reviewed commit 一致。

## 结论

未发现阻断规格批准或代码质量批准的问题。该区间已经形成与已确认设计、Task 1-8 实施计划相符的自治工程控制平面：状态外置、原子恢复、Git/worktree 边界、保护路径、质量门、脱敏诊断、有限重试、受控 Codex adapter、故障演练和只读治理 CI 均有实现与负向测试支撑。

GitHub Actions run `38064236869` 已通过 API 复核，结论为 `success`，`headSha` 精确等于 reviewed commit。两个 Job 均成功，未发现以浅历史、宿主 Python 回退、真实 Codex 或可写 GitHub 权限绕过验收的情况。

## 重点复核

### Hosted Python 信任链

- workflow 固定 `actions/checkout` 与 `actions/setup-python` 到完整 commit，并在两个 Job 中将 setup-python 约定的 `pythonLocation` 限制为 Python 3.11 hosted-toolcache 布局。
- CI 在调用 wrapper 前收紧 `/opt/hostedtoolcache/Python`、版本父目录、`pythonLocation`、`bin` 和最终 `python3.11` 的 group/world write 位；随后通过 `scripts/agent-harness --python --version` 验证实际进入 Python 3.11。
- shell bootstrap 不使用调用者 PATH，且对 `pythonLocation` 做单值、固定前缀、数字 patch 约束；空格、tab、换行等拼接输入不能进入候选链。
- shell 层绑定每个 symlink 节点和最终文件的 device、inode、owner、mode；Python 层重新解析并比较完整 fingerprint，同时校验最终 `sys.executable`、版本化 basename、Python 3.11-3.14 范围和父目录权限。
- `bin/python` 为 symlink 和普通文件的合法 hosted 布局均有固定 digest Linux 测试；跨 minor symlink、可写目标、路径逃逸、身份变化和含分隔符环境值均有拒绝测试。

结论：hosted Python 入口建立了明确的宿主信任根和双层复验，没有将任意环境路径或未验真的 `python3` 作为 CI 解释器。

### Fake Codex CI 隔离

- 默认 fault-drill 入口只运行 fake drills；真实 Codex 仅能经显式 `--allow-real-codex` 进入一次性 smoke 路径。
- unit Job 明确执行 `--exclude-substring linux_wrapper`，使用确定性 fake tools；integration Job 只执行 `--include-substring linux_wrapper`，并显式传入固定镜像 digest、`--ci` 和 `/usr/bin/docker`。
- autonomous-loop 测试同时 patch `build_codex_command` 与 `run_codex`，避免测试因宿主 Codex 存在而触发真实工具发现或真实执行。
- workflow 无 `codex exec`，无 dangerous bypass 参数，无 `contents: write`、PR 写权限、push、merge、release 或 deploy 步骤。
- 本次审查未运行真实 Codex；本地验证仅运行受控 Python 单元测试、文档漂移检查、危险参数扫描和 Git diff 检查。

结论：CI 的 fake 工具路径、Linux wrapper 集成路径和显式 real-Codex smoke 路径边界清楚，当前 workflow 不会隐式运行真实 Codex。

### 工具身份指纹

- `_BoundTool` 记录 entry、resolved target、目录、信任根和 symlink chain；身份字段包含 `st_dev`、`st_ino`、`st_uid`、`st_mode`、`st_size` 与 `st_ctime_ns`。
- 工具在发现时绑定，在构建受控 PATH 或实际执行前再次检查 entry、target、chain、owner、mode、可执行位、父目录和仓库/临时目录排除规则。
- 新增 size 与 ctime 后，同长度替换、原 inode 内容修改和 symlink chain 变化不能沿用旧绑定；相关 tampering/identity tests 已覆盖。
- 环境只传递经过验证的最小键集合；HOME/TMP、locale 和 timezone 均有格式或绝对路径约束，不继承调用者任意 PATH、Python site 或秘密变量。

结论：工具绑定不是仅按名称或 PATH 的瞬时检查，最终执行前的身份复验符合控制平面威胁模型。

### 完整历史 checkout

- 两个 GitHub Job 的 checkout 均设置 `fetch-depth: 0`，且 action 固定到完整 SHA。
- 这保证 review evidence 的 `merge-base --is-ancestor`、run ownership、重写历史和浅历史拒绝逻辑在 hosted runner 上拥有完整提交图，不会因默认 shallow checkout 产生假失败或跳过验证。
- governance test 要求恰好两个 checkout block 都包含 `fetch-depth: 0`；移除任一配置会使测试失败。

结论：`79e85b72` 关闭了 hosted runner 缺少 review ancestry 的最后一个确定性缺口。

### 交付证据链

- review evidence 校验要求 security、DX/CI、whole-branch 三个唯一 reviewer、唯一 tracked path、唯一 SHA-256、reviewed commit/range 一致、base 为 commit 的真实 ancestor，以及报告 EOF 的唯一双 APPROVED canonical block。
- delivery manifest、计划 checkbox、ledger 状态和多份文档 marker 由 `--check-docs` 联合校验；外部同步只能按 review -> GitHub -> Obsidian 的状态顺序推进。
- GitHub run `38064236869` 的 `headSha`、分支、事件和两个 Job 结论已独立查询确认，不能由旧 run 替代当前 reviewed commit 的 hosted 证据。
- 当前 tracked `HARNESS_DELIVERY.json` 和 `docs/agent/reviews/` 仍绑定 `1fb39677`。这是本轮三路最终审查完成前的预期过渡状态，不构成对 `79e85b72` 的 canonical 完成交付证明。交付控制器必须在收集本报告及另外两份同范围报告后，将 implementation/evidence/review hashes、Task 8 Step 4/8/9、GitHub 状态和后续 Obsidian 状态按真实证据写回；在此之前不得宣称 `fully_synchronized`。

结论：实现具备可验证的证据链约束，本轮批准绑定 `79e85b72`；tracked canonical 交付状态仍需由控制器完成换绑和同步，不能复用旧 `1fb39677` 审查摘要冒充本轮证据。

## 验证证据

- GitHub API：run `38064236869`，`status=completed`，`conclusion=success`，`headSha=79e85b7208d9647781e978e2dff90158d28f2f0a`。
- GitHub unit Job `114248522554`：controlled Python 验证、配置测试、deterministic fake-tool tests、fault drills、forbidden scan、docs check 全部 success。用户提供的运行摘要记录 unit 401；当前同 commit 本地 non-Linux 分区因包含最终 governance ancestry test 实际发现并通过 402 项，两者不影响零失败结论，但控制器写回时应以可归档的 GitHub Job 日志为 hosted 计数真源。
- GitHub integration Job `114248853578`：固定 digest 镜像准备与 Linux wrapper integration 全部 success；用户提供的运行证据为 8/8、zero skip。
- 本地：`scripts/agent-harness --python tools/agent-harness/run_tests.py --exclude-substring linux_wrapper --fail-on-skip`，`Ran 402 tests`，`OK`，零 skip。
- 本地：`scripts/agent-harness --python tools/agent-harness/drills/run_drills.py --check-forbidden-options`，`PASS forbidden-option-policy`。
- 本地：`scripts/agent-harness --python tools/agent-harness/drills/run_drills.py --check-docs`，`PASS plan-memory-drift`。
- 本地：`git diff --check c7b9366142ab6f8b0570d208c156fc0392c007f1..79e85b7208d9647781e978e2dff90158d28f2f0a`，退出码 0。
- 未运行 Docker 本地集成；采用与 reviewed commit 绑定的 GitHub Ubuntu 24.04 integration Job 作为 Linux zero-skip 证据。
- 未运行真实 Codex。

## 发现

无阻断发现。

非阻断交付条件：本报告位于控制器指定的 ignored `.superpowers/sdd` 路径，尚不是 tracked canonical evidence。控制器必须完成三路报告收集、digest 计算、manifest/计划/项目文档同步，并重新运行治理检查；若任何 tracked 实现文件在 `79e85b72` 之后变化，本批准自动失效并需重新审查。

SPEC_COMPLIANCE=APPROVED
CODE_QUALITY=APPROVED
