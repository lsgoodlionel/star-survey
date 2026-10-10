# Task 8 whole-branch Python entry final scoped re-review

日期：2026-10-10

角色：whole-branch reviewer

最终候选：`1fb396777cce6274a44989755e6859fa2a3b2ed5`

完整控制平面范围：`c7b9366142ab6f8b0570d208c156fc0392c007f1..1fb396777cce6274a44989755e6859fa2a3b2ed5`

本轮修正范围：`b1b5d7c0b954bead0cad6eeb7af4bf8e4ea323b7..1fb396777cce6274a44989755e6859fa2a3b2ed5`

方式：只读检查实际 diff、父提交 fixture、wrapper 实现链、固定 digest Docker integration、controller manifest/plan 状态与工作区；未修改 tracked 文件，未 commit/push，未运行真实 Codex。

## 结论

未发现需阻断的问题。先前指出的 newline fixture 缺陷已精确关闭，修正范围保持最小且只改一项 Linux integration 测试。候选 `1fb39677` 可作为 whole-branch 最终候选交给 controller 在三份报告齐备后统一更新 manifest/docs。

## Newline fixture 关闭情况

- 父提交 `b1b5d7c0` 使用 `"$(printf '\n')"`；POSIX command substitution 会删除尾随换行，因此第三轮 separator 实际为空。这项旧证据不能证明 newline 输入，先前 finding 成立。
- 当前测试在 `tools/agent-harness/tests/test_codex_adapter.py:512` 使用多行单引号赋值创建字面 newline，并在循环中以 `"$newline"` 作为第三个 separator。该写法在 POSIX `/bin/sh` 中保留一个换行字符。
- 每轮 wrapper 调用前新增 `test "${#separator}" -eq 1 || exit 95`。space、tab、newline 任一 fixture 退化为空或多字符都会显式失败，不再可能以三次输出计数掩盖 fixture 失真。
- 独立 `/bin/sh` probe 对三轮 separator 输出 `1, 1, 1`；固定 digest integration 中 `test_linux_wrapper_never_splits_untrusted_python_location` 通过，证明真实测试环境也消费了该断言。
- 原 marker 断言与三次 `Python 3.11` 输出计数仍在：测试同时证明 hostile split candidate 未执行，并证明三种输入均通过可信 fallback 完成。

## 增量与回归复核

- `b1b5d7c0..1fb39677` 仅修改 `tools/agent-harness/tests/test_codex_adapter.py`，为 4 insertions / 1 deletion；wrapper、workflow、manifest 和生产 Python 选择逻辑均未变化。
- `git diff --check` 对修正范围及完整范围均通过；`sh -n scripts/agent-harness` 通过。
- `HEAD` 精确等于 `1fb396777cce6274a44989755e6859fa2a3b2ed5`；完整范围 base 与修正范围 base 均确认是候选祖先。
- `37f86a8d..b1b5d7c0` 的安全修复仍完整：hosted root patch 必须为纯数字；`pythonLocation` 只生成一个 `$pythonLocation/bin/python` 候选；候选以单个 quoted argument 传入；fallback 为固定字面路径列表。
- hosted 候选仍受 trusted-root/owner/mode/symlink-chain 检查、bootstrap fingerprint、`sys.executable` identity 和 Python 3.11-3.14 版本范围绑定。newline test 修正没有改变这些控制。

## 测试证据

- 本轮 fresh 固定 digest integration：`8/8 PASS`，零 skip，耗时 6.621s；镜像为 `python:3.11-slim@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce`，并启用 `--ci --fail-on-skip` 与受控 `/usr/local/bin/docker`。
- 该 8 项覆盖 hosted regular entry、hosted symlink entry、bootstrap identity mutation、mixed help policy、纯 help、trusted `/usr/bin` symlink、escaped/writable candidate，以及本轮 space/tab/newline field-splitting 回归。
- 完整 Harness 证据保持为 408 项：非 Linux 分区 400 pass；本地常规运行的 8 项为有类型的 Docker integration skips；固定 digest 分区单独 8/8 pass、零 skip。当前修正只影响这 8 项中的一个 fixture，且已在真实固定 digest 环境 fresh 重跑。
- Codex Security 本轮 diff scan 完整覆盖唯一变更文件，无 reportable finding 或 deferred surface。

## 状态诚实性

- `docs/agent/HARNESS_DELIVERY.json` 仍绑定上一轮 controller adjudication 的 `ef5e1d3b`、既有三份 canonical reports 与 `github=pending`；它没有把 `1fb39677` 冒充为已 promotion，也没有把本地 Docker 结果冒充 GitHub hosted 成功。
- 历史 GitHub run `38059629731` 仍是 setup-python entry 失败证据，不是成功证据；本轮报告没有改变或重写该事实。
- 当前 `1fb39677` 尚未写入 tracked controller manifest/docs，符合“新 security、DX/CI、whole-branch 三份报告齐备后再由 controller 更新”的 pending 流程，不构成遗漏。
- 报告路径未被 Git 跟踪；覆盖本报告后 `git status --short` 仍为空。未产生 tracked/staged 修改，未 commit/push，未运行真实 Codex。

SPEC_COMPLIANCE=APPROVED
CODE_QUALITY=APPROVED
