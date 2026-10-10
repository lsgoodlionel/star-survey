<!-- harness-delivery-status: locally_reviewed_sync_pending -->
# Task 8 审查修复报告

日期：2026-10-10

## 交付状态

Task 8 round-5 最终自动审查修复已在本地完成，implementation commit 为
`53c1f3db577c3d505ce582a53f45800e84057636`。当前结构化状态为
`local_validated_sync_pending`：独立复审、GitHub 与 Obsidian 均为 `pending`，因此 Task 8 与总计划保持
`in_progress`，不宣称完整交付、CI 已运行或完整同步。

本任务未修改产品业务代码，未 push，未更新 Obsidian，也未派生 subagent。

## RED→GREEN

本轮先逐项建立可复现 RED，再作最小修复：

- 最小工具依赖：旧 `doctor`/`resume` 和 wrapper 会全局要求 Codex、Java、Docker、Node；新增按操作依赖测试后，
  `doctor/init` 只需 Python 3.11+ 与 Git，`run-codex` 才要求 Codex，Gate 仅要求自身命令。旧 Python 现在以退出码 2
  输出简洁版本诊断，不再 traceback。
- Linux fixture：新增 Docker CLI 缺失、daemon/权限错误、镜像缺失三类结果；workflow 固定 Python 3.11 和镜像 digest，
  CI 通过 `--fail-on-skip` 要求 0 意外 skip。
- secret preflight：从只看部分文件改为合并 tracked、untracked、ignored inventory，并对每个路径 `lstat`/resolve；
  拒绝排除目录内秘密、目录 symlink 和解析逃逸。`.env.example` 等模板仅由 `HARNESS_HOST.json` allowlist 放行。
- real smoke：plan/fixture 在写入前拒绝既有 symlink，使用防跟随临时文件、`fsync` 和原子 replace；cleanup 检查每个
  return code，并验证 worktree registration、branch ref 和路径均不存在。`completed` 要求 fixture 精确内容、非空 required
  Gates 全部 PASS、exit 0、evidence 存在且绑定当前 HEAD；`paused` 要求存在脱敏 terminal history。
- forbidden scan：覆盖 `.yml`/`.yaml`、多行 Codex context、bypass、`danger-full-access`、`--add-dir`、config/profile/
  feature overrides；无关的 Docker `--config` 不误报。
- docs drift：新增 `HARNESS_DELIVERY.json`，统一绑定 active plan、逐 Task checkbox、ledger、implementation commit、
  evidence/status 与 AGENTS/AUTONOMY/README/PROJECT_MEMORY marker；外部三项未全 complete 时不能进入 fully synchronized。
- portability：active plan、secret policy、fixture scope 和宿主文档契约移入 `HARNESS_HOST.json`；临时独立 Git 仓库测试
  证明自定义 host config/plan/ledger/docs 可运行，real smoke 也会把自定义 config 路径传入 disposable worktree。
- 端到端 success drill：使用真实 `RunService` 完成 init、fake adapter 受控改动和带 run-id 提交、Gate、review evidence、
  finalize 与 history，不再用手工 transition 代替成功链路。

Round 2 继续先建立可复现 RED，再作最小修复：

- `atomic_create` 从可信 root fd 逐组件使用 `dir_fd`、`O_DIRECTORY`、`O_NOFOLLOW`，相对执行 mkdir/stat/create/replace/fsync，
  并在发布前后复验 inode；父目录 symlink swap 不会覆盖 root 外目标。
- Codex workflow shell 先拒绝 quote、变量、命令替换、反斜杠续行和 shell 控制符，再只接受规范化的
  `codex exec --sandbox workspace-write` argv allowlist；未知 option 与多个 prompt 均 fail closed。
- cleanup 每个 Git 操作和路径后置检查独立捕获 `OSError`，累积 typed error 并继续执行后续清理。
- workflow 拆成 fake-tool unit 与显式 Docker integration job；setup-python 的绝对路径会成为最终 runtime 并再次验真，
  旧 Python 用例改为确定性注入，不依赖 Ubuntu 系统版本。
- portable 测试复制 core、drills、wrapper、模板和 host config 到独立临时仓库，并从复制品运行 wrapper 与 fake smoke；
  plan root 统一为 `docs/superpowers/plans`。
- success drill patch `agent_harness.run_service.run_codex` 后真实调用 `RunService.run_autonomous`，断言 adapter、Gate HEAD、
  review scope、finalize/history 的事件顺序。

Round 3 同样先建立可复现 RED，再作最小修复：

- workflow run-block 抽取接受合法的 quoted `run` key，再用 stdlib `shlex` 规范化 token；`co""dex` 可识别，变量、命令替换、
  反斜杠与控制语法在任何 Codex-like 命令中 fail closed，同时保留无关 `echo "codex"`。
- `TemporaryDirectory.cleanup()` 和 primary identity check 独立捕获并累积 typed error；cleanup 抛出 `OSError` 时仍无条件检查
  主工作树身份，任何异常都不能产生 `cleanup=verified`。
- hosted-toolcache fixture 使用真实 Python 3.11 executable，不再用改变 `sys.executable` 身份的 shell shim；bootstrap 与最终
  runtime 均重新验真同一受信解释器。
- `run_tests.py` 只接受显式 `--ci`、固定镜像 digest 和 allowlisted Docker 绝对路径；workflow 使用 `/usr/bin/docker`，
  macOS 以受信 `/usr/local/bin/docker` 完成同一 integration 契约。
- portable 测试在独立仓库生成并提交 active plan、manifest、ledger、status markers 与 fixture，经复制后的 wrapper 运行
  `--check-docs` 和 fake autonomous smoke；缺 plan、错 root 或未读取指定 config 均有 RED。
- manifest 新增 controller-only review evidence schema；pending 时报告必须为空，complete 时必须验证三份 tracked report、
  reviewed commit/range、内容 digest 和双 APPROVED。Step 7 保持 pending。
- 零 skip integration 首次真实执行发现混合 help 参数可令 argparse 提前退出；已有 RED 捕获后，wrapper 对非纯 help 组合
  fail closed，重跑 7/7 PASS。

Round 4 先以独立最小 fixture 重现 round-3 finding，再作受限实现：

- workflow parser 只接受 block-style `run:`、literal quoted `run` key、inline scalar 与 `|`、`>`、`|-`、`>-`；flow mapping、
  escaped key、tag、anchor/alias、其他 scalar indicator 和未解析但含 Codex-like `exec` 的表示全部 fail closed。
- Codex shell 只接受裸 executable；`command`、`env`、`env NAME=value`、绝对路径或其他 wrapper 全部拒绝，同时保留无执行
  语义的 `echo "codex"`。
- review evidence 只解析报告 EOF 的 canonical verdict block；两个 key 全文各出现一次、顺序固定、值与 manifest 一致且都为
  `APPROVED`。历史/引用/code block APPROVED、后续 CHANGES_REQUIRED、重复、缺失、逆序与尾随文本均拒绝。
- `docs/agent/HARNESS_DELIVERY.json` 继续保持 `independentReview: pending` 和空 `reviewEvidence.reports`；计划 Step 7 未勾选。

Round 5 继续严格 RED→GREEN，并关闭 round-4 的全部 finding：

- quoted inline `run` scalar 先按受支持的 single/double YAML 子集解码，folded `>` block 按逻辑单行处理；tag、flow、alias、
  malformed quote 等不支持表示继续 fail closed。
- shell 检查只判断可执行位置，递归识别 `sh`/`bash`/`zsh -c`、`eval`、`command`/`env`、绝对路径及 glob/brace/path
  expansion；普通 metadata、`grep`/`python` 数据参数和安全 nested shell 不误报。
- workflow 拒绝包含仓库相对路径与 1-based `run` 行号，只输出拒绝类别，不回显命令或测试 secret。
- review verdict 必须是由空行分隔的顶层 EOF plain-text sentinel，不得位于 fence、HTML comment、indented code、blockquote/
  lazy blockquote 或 nested container；重复、逆序和后续 verdict 继续拒绝。
- 三个 reviewer 必须绑定三份不同的 tracked canonical path 和不同 digest；symlink alias 明确拒绝。

## Round 5 提交与文件

- 实现提交：`53c1f3db577c3d505ce582a53f45800e84057636`。
- 证据/docs：本报告所在后续提交；准确 SHA 由最终交接记录，避免文档自引用 commit。
- 实现文件：`tools/agent-harness/drills/run_drills.py`、`tools/agent-harness/tests/test_fault_injection.py`。
- 证据与说明：`docs/agent/HARNESS_DELIVERY.json`、`tools/agent-harness/README.md`、本实施计划、平台 README/进度及本 report/ledger。

## 验证证据

- Task 8 focused：103 tests，96 PASS、7 typed skip、0 failure，44.533s。
- 完整 Harness：406 tests，399 PASS、7 typed skip、0 failure，349.608s。7 个本地 sandbox skip 均为
  `linux-fixture:permission-denied`。
- 固定 digest Docker integration：首次在 sandbox 内 7 项因 socket `permission-denied` 失败；以同一镜像和受信
  `/usr/local/bin/docker` 在 sandbox 外重跑 7/7 PASS、零 skip，5.388s。该本地结果不替代 GitHub hosted run。
- Fake drills：11/11 PASS；success flow 为
  `init → fake-adapter → gate → review → finalize → history`，终态 `completed`。
- 受控入口：Python 3.12.13；宿主 `/usr/bin/python3` 低于 3.11 时返回简洁诊断、exit 2、无 traceback。
- Doctor：0 error；clean worktree 下 22 项 ok、4 项 warning。受控 PATH 内未发现 Docker、Node、Codex，Java 非 21；
  这些可选工具未阻止纯治理启动，Docker integration 另由显式受信绝对路径验证。
- Productization：37/37 PASS；capability map current。
- Governance：round-5 forbidden-option policy、plan/memory docs drift 与 13/13 governance tests 均 PASS。
- `git diff --check`：PASS。

## 唯一真实 smoke

本轮没有重跑真实 Codex。Task 8 先前恰好一次真实 cycle 的事实保持不变：

- 终态 `paused`；changed paths 0；passed Gates 0。
- disposable run-history 在清理前存在并通过脱敏检查。
- disposable worktree 和 branch 已删除，主 worktree identity 未变化，未接触生产或 release。
- 当次旧摘要未保留 stop classification，所以不能推断登录、配额或 CLI 的具体暂停原因。

新代码为将来的 smoke 增加 exact fixture、Gate/HEAD/evidence 和 cleanup postcondition，但这些新断言只有自动测试证据；
不得据此把上述真实 `paused` 叙述成真实 `completed`。

## 残余顾虑

- round-4 三份复审仍为 `CHANGES_REQUIRED`；round-5 修复后仍须由控制器取得同一 commit/range 的三路双 APPROVED，
  当前没有任何 round-5 approval，不能提前提升 independentReview 或 Step 7。
- 本机固定镜像 integration 已 7/7 零 skip；GitHub Ubuntu 24.04 的绑定 run 仍未执行，Task 8 Step 4 不能勾选。
- GitHub push 与 Obsidian 同步明确未执行。
- 唯一真实 smoke 的具体暂停分类不可恢复，且按“一次真实 cycle”约束不重跑。

报告不包含秘密、原始 Codex JSONL 或未脱敏运行时证据。

## Controller adjudication and final closure

五轮自动修复后，控制器没有继续无界重试，而是把工作流扫描器收窄为文档化的 stdlib-only 保守语法。最终 implementation
commit 为 `9031fcd5e0322b16d5df2ae42580af946a9d796d`；测试证据修正 commit 为 `77422233`。最终 focused suite 为
104 tests（97 PASS、7 typed skip），whole-branch reviewer 的 full Harness 为 407 tests（400 PASS、7 typed skip），本机固定
digest Docker integration 为 7/7 PASS、零 skip，fake drills 11/11 PASS。

Security、DX/CI、whole-branch 三份 final closure report 均绑定
`c7b9366142ab6f8b0570d208c156fc0392c007f1..9031fcd5e0322b16d5df2ae42580af946a9d796d`，双 verdict 为
`APPROVED`。结构化 manifest 已将 independent review 标为 complete；GitHub hosted run、push 与 Obsidian 仍保持 pending。
