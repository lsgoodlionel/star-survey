<!-- harness-delivery-status: local_validated_sync_pending -->
# Task 8 审查修复报告

日期：2026-10-10

## 交付状态

Task 8 的实现和审查修复已在本地完成，implementation commit 为
`37a9bea07d409e703c067acdf580c5ee7ced5b35`。当前结构化状态为
`local_validated_sync_pending`：独立审查、GitHub push 和 Obsidian 均为 `pending`，因此 Task 8 与总计划保持
`in_progress`，不宣称完整交付或完整同步。

本任务未修改产品业务代码，未 push，未更新 Obsidian，也未派生 subagent。

## RED→GREEN

本轮先逐项建立可复现 RED，再作最小修复：

- 最小工具依赖：旧 `doctor`/`resume` 和 wrapper 会全局要求 Codex、Java、Docker、Node；新增按操作依赖测试后，
  `doctor/init` 只需 Python 3.11+ 与 Git，`run-codex` 才要求 Codex，Gate 仅要求自身命令。旧 Python 现在以退出码 2
  输出简洁版本诊断，不再 traceback。
- Linux fixture：新增 Docker CLI 缺失、daemon/权限错误、镜像缺失三类结果；workflow 固定 Python 3.11 和镜像 digest，
  CI 通过 `--fail-on-skip` 要求 0 意外 skip。本机无 Docker CLI，相关 7 项以同一 typed reason skip。
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

## 验证证据

- Task 8 focused：33/33 PASS。
- 完整 Harness：379 tests，372 PASS，7 typed skip，0 failure，332.692s。7 个 skip 均为本机
  `linux-fixture:docker-cli-missing`；CI 会将任意 skip 判为失败。
- Fake drills：11/11 PASS；success flow 为
  `init → fake-adapter → gate → review → finalize → history`，终态 `completed`。
- 受控入口：Python 3.12.13；宿主 `/usr/bin/python3` 低于 3.11 时返回简洁诊断、exit 2、无 traceback。
- Governance：forbidden-option policy 与结构化 plan/memory drift check 均 PASS。
- Doctor：0 error；21 项 ok。warning 为本机缺 Docker、Node、Codex，Java 非 21，以及提交前工作区有文档改动；
  这些可选工具未阻止 doctor 启动。
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

- 用户禁止 subagent，三个独立 reviewer gate 未执行；实现者完成的安全、DX/CI 和全分支复核不能替代独立审查。
- 本机缺 Docker CLI，7 个 Linux wrapper fixture 无本地执行证据；固定镜像与 CI 零 skip 策略需由 GitHub runner 验证。
- GitHub push 与 Obsidian 同步明确未执行。
- 唯一真实 smoke 的具体暂停分类不可恢复，且按“一次真实 cycle”约束不重跑。

报告不包含秘密、原始 Codex JSONL 或未脱敏运行时证据。
