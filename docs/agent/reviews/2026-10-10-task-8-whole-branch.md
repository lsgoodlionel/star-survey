# Task 8 whole-branch hosted-runtime final closure review

日期：2026-10-10
仓库：`/Users/lionel/Develop/survey/.worktrees/admin-web`
Implementation commit：`ef5e1d3b61f2944731ba1be6aa5996548a88cf31`
Reviewed range: `c7b9366142ab6f8b0570d208c156fc0392c007f1..ef5e1d3b61f2944731ba1be6aa5996548a88cf31`
重点补丁：`717c1d82cbe77ccd55cf19a4949583ab03c9571b..ef5e1d3b61f2944731ba1be6aa5996548a88cf31`
方式：只读 diff/代码/状态审查、focused unit/governance tests、固定 digest Docker integration、独立 hosted-toolcache 正反 probes 与状态一致性检查；未修改 tracked 文件、未 push、未运行真实 Codex

## Findings

无残余 finding。

## Prior Finding Closure

### P1 hosted runtime identity/version binding：已关闭

- `/Users/lionel/Develop/survey/.worktrees/admin-web/scripts/agent-harness:178-230` 仍先复验完整 symlink chain、owner、mode、trusted parents 与 shell 阶段 fingerprint；随后对 hosted root 或 versioned bootstrap 无条件要求 resolved `sys.executable` 等于 resolved target。
- 当 resolved target basename 为 `python3.11` 至 `python3.14` 时，`/Users/lionel/Develop/survey/.worktrees/admin-web/scripts/agent-harness:220-229` 同时要求实际版本处于支持范围，且 target minor 与 `sys.version_info` 一致。上一轮可接受 `python3 -> python3.12`/实际 3.11 的双分支绕过不再存在。
- 非 hosted 的旧 unversioned `/usr/bin/python3` 只可作为进入受控 discovery 的 bootstrap；`trusted_runtime()` 仍要求最终 unversioned runtime 与当前 resolved `sys.executable` 一致且为 Python 3.11-3.14，否则继续寻找并复验独立的 versioned trusted runtime。这保留了旧系统 Python 启动发现、但不把旧版本直接选作 Harness runtime 的既有行为。
- 独立固定 digest 正向 probe：在 hosted `3.11.99/x64/bin` 中令 `python3 -> python3.11`，移除 fallback 后返回 exit 0、`Python 3.11.17`。
- 独立固定 digest负向 probe：令 `python3 -> python3.12`，但 target 实际为 Python 3.11，移除 fallback 后返回 exit 2 与 `Python bootstrap 身份变化`。新增 regression 位于 `/Users/lionel/Develop/survey/.worktrees/admin-web/tools/agent-harness/tests/test_codex_adapter.py:471-492`，精确覆盖该正反边界。

### P2 status documentation drift：已关闭

- `/Users/lionel/Develop/survey/.worktrees/admin-web/docs/agent/AUTONOMY.md:5-7` 现在与 manifest 一致：三路独立复审 complete，GitHub hosted 零 skip run 与 Obsidian pending，并保留 controller-only promotion 约束。
- `/Users/lionel/Develop/survey/.worktrees/admin-web/.superpowers/sdd/2026-10-10-autonomous-engineering-control-plane/task-8-report.md:6-13` 的顶部当前状态已更新为获批 implementation `9031fcd5`、`locally_reviewed_sync_pending`、独立复审 complete、GitHub hosted run 与 Obsidian pending；不再与后文 controller adjudication 相反。
- `/Users/lionel/Develop/survey/.worktrees/admin-web/platform/docs/p2/progress.md:739-742` 已移除未完成的 round-3 残句，限制项现在完整陈述三路 review 已批准而 hosted run 尚未绑定。

## Verification Evidence

- 固定 digest Docker integration：7/7 PASS、0 skip，5.541s；镜像为 `python:3.11-slim@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce`。
- Focused adapter：45 tests，38 PASS、7 个本地 sandbox `linux-fixture:permission-denied` typed skip、0 failure，27.224s；上述 7 项已由 sandbox 外同 digest 零 skip integration 全部补齐。
- Governance：13/13 PASS；controller review evidence、read-only workflow、固定 action、Python 3.11、unit/integration partition 与 fail-on-skip 契约均保持通过。
- `--check-docs` 返回 `PASS plan-memory-drift`；当前 workflow scan 返回 `PASS forbidden-option-policy`。
- `git diff --check` 对重点补丁与完整 reviewed range均通过；重点补丁只修改 wrapper、其 targeted tests 与三份状态文档。
- 既有 whole-branch control-plane implementation、407-test full regression、11/11 fake drills、37/37 productization 与 canonical review evidence 已在前序批准中验证；本补丁未改动这些核心路径或契约。

## Status Truthfulness

- `/Users/lionel/Develop/survey/.worktrees/admin-web/docs/agent/HARNESS_DELIVERY.json:2-23` 仍为 `locally_reviewed_sync_pending`：先前 implementation `9031fcd5` 的三路 independent review complete，GitHub 与 Obsidian pending。hosted-runtime patch 尚未被伪装成 hosted success，也未提前写入 controller evidence。
- `/Users/lionel/Develop/survey/.worktrees/admin-web/docs/agent/HARNESS_DELIVERY.json:25-34` 与计划 `/Users/lionel/Develop/survey/.worktrees/admin-web/docs/superpowers/plans/2026-10-10-autonomous-engineering-control-plane.md:433-504` 继续保持 Task 8 Step 4/8/9 与 Final Acceptance 6/7 pending；Step 7 仅代表已绑定的先前三路 review evidence。
- 已知 GitHub run `38058476411` 仍是失败证据，不被描述为成功。当前修复尚未形成绑定的 GitHub Ubuntu 24.04 success run，因此 Step 4 和 GitHub external sync 保持 pending 是诚实状态。
- 唯一真实 Codex cycle 仍记录为 `paused`、changed paths 0、passed Gates 0；本轮没有重跑真实 Codex。Obsidian 仍 pending，Task 8 与总计划保持 `in_progress`。

SPEC_COMPLIANCE=APPROVED
CODE_QUALITY=APPROVED
