<!-- harness-delivery-status: local_validated_sync_pending -->
# Task 8 审查修复报告

日期：2026-10-10

## 交付状态

Task 8 三轮审查修复已在本地完成，implementation commit 为
`09e84c0aa005538565eb85b68c8949b5a3064612`。当前结构化状态为
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

## 验证证据

- Task 8 focused：92 tests，85 PASS、7 typed skip，44.018s；同 7 项随后在 Docker integration 7/7 PASS、0 skip，5.085s。
- 完整 Harness：395 tests，388 PASS、7 typed skip、0 failure，350.268s。7 个本地 sandbox skip 均为
  `linux-fixture:permission-denied`；固定 digest integration 已在 sandbox 外实际通过，CI 仍会将任意 skip 判为失败。
- Fake drills：11/11 PASS；success flow 为
  `init → fake-adapter → gate → review → finalize → history`，终态 `completed`。
- 受控入口：Python 3.12.13；宿主 `/usr/bin/python3` 低于 3.11 时返回简洁诊断、exit 2、无 traceback。
- Governance：forbidden-option policy 与结构化 plan/memory drift check 均 PASS。
- Doctor：0 error；clean worktree 下 22 项 ok、4 项 warning。受控 PATH 内未发现 Docker、Node、Codex，Java 非 21；
  这些可选工具未阻止纯治理启动，Docker integration 另由显式受信绝对路径验证。
- Productization：37/37 PASS；capability map current。
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

- security、DX/CI 和 whole-branch rereview-2 均为 `CHANGES_REQUIRED`；round 3 修复后仍须由控制器取得三路双 APPROVED。
- 本机固定镜像 integration 已 7/7 零 skip；GitHub Ubuntu 24.04 的绑定 run 仍未执行，Task 8 Step 4 不能勾选。
- GitHub push 与 Obsidian 同步明确未执行。
- 唯一真实 smoke 的具体暂停分类不可恢复，且按“一次真实 cycle”约束不重跑。

报告不包含秘密、原始 Codex JSONL 或未脱敏运行时证据。
