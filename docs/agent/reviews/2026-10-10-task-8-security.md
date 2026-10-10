# Task 8 security Python entry scoped re-review

日期：2026-10-10

角色：shell 输入处理与运行时身份边界 reviewer

最终候选：`1fb396777cce6274a44989755e6859fa2a3b2ed5`

完整范围：`c7b9366142ab6f8b0570d208c156fc0392c007f1..1fb396777cce6274a44989755e6859fa2a3b2ed5`

证据修正：`b1b5d7c0b954bead0cad6eeb7af4bf8e4ea323b7..1fb396777cce6274a44989755e6859fa2a3b2ed5`

方式：只读 Git diff、源码、测试和固定 digest Linux integration 复核；未修改 tracked 文件，未 commit/push，未运行真实 Codex。

## Findings

无阻断 finding。

## Scoped evidence correction

- 唯一增量是 `tools/agent-harness/tests/test_codex_adapter.py:512-515` 的测试证据修正，没有修改生产 wrapper 或运行时实现。
- 旧写法 `"$(printf '\\n')"` 受 POSIX command substitution 规则影响，会删除尾随 newline，实际向循环传入空字符串，不能证明 newline 输入已覆盖。
- 新写法用跨行单引号赋值：`newline='`、字面换行、`'`。单引号保留该字符，不经过 command substitution 的尾随换行删除。
- 循环以 `"$newline"` 传递单一参数，并在每次迭代先执行 `test "${#separator}" -eq 1 || exit 95`。空字符串、多个字符或意外展开都会在运行 wrapper 前失败。
- 测试仍断言三次可信 `Python 3.11.*` 输出，并在每次调用后确认攻击者 marker 不存在。结合三种 separator 的长度断言，空格、Tab、字面 newline 三条路径均实际执行且均未选择恶意入口。

## Shell 输入边界

- `scripts/agent-harness:133-142` 仅在 `AGENT_HARNESS_CI=1` 时考虑 hosted 候选；完整值先匹配 `/opt/hostedtoolcache/Python/3.11.*/x64`，再剥离固定前后缀并以 `''|*[!0-9]*` 拒绝空 patch 或任何非数字字符。
- 空格、Tab、newline、额外 slash、字母及其他路径拼接均不能形成 `ci_candidate`。patch component 仍严格为非空纯数字。
- `try_bootstrap_candidate "$ci_candidate"` 把 `pythonLocation` 作为单一路径参数传递；函数以 `candidate=$1` 接收，后续文件系统消费点保持引用。不存在未引用变量扩展导致的 field splitting。
- hosted 候选无效时只进入静态版本化 fallback 列表；恶意路径 marker regression 证明三种空白输入都不会执行攻击者文件。

## Runtime identity boundary

- setup-python hosted entry 仍为 `$pythonLocation/bin/python`；普通文件 copy 与 `bin/python -> python3.11` symlink 两种合法布局均通过同一 fingerprint、owner/mode、trusted parent 与 canonical root 校验。
- `trusted_bootstrap()` 重建 symlink chain 并比较 device/inode/owner/mode fingerprint。hosted 或 versioned target 必须与当前 `sys.executable` 的 resolved identity 相同。
- 若 target 名为 `python3.N`，`N` 必须与实际 `sys.version_info` minor 一致；hosted 实际版本与最终 runtime 均继续限制为 Python 3.11-3.14。实际 3.11 冒充 `python3.12` 的 fixture 仍失败关闭。
- 本次测试提交未改动 candidate 列表、trusted roots、owner policy、symlink policy、identity/minor binding 或 runtime selection，因而没有放宽非 hosted 行为。非 hosted unversioned `python` 仍不被接受，`/usr/bin/python3` 仅保留受控 discovery 兼容路径。

## Verification evidence

- 用户提供的固定 digest integration：8/8 PASS，0 skip。
- reviewer 独立复跑固定 digest Linux wrapper integration：8/8 PASS，0 skip，使用 `python:3.11-slim@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce`；其中 newline 长度断言与三次输出断言均通过。
- `sh -n scripts/agent-harness`：PASS。
- `git diff --check c7b9366142ab6f8b0570d208c156fc0392c007f1..1fb396777cce6274a44989755e6859fa2a3b2ed5`：PASS。
- `git diff --check b1b5d7c0b954bead0cad6eeb7af4bf8e4ea323b7..1fb396777cce6274a44989755e6859fa2a3b2ed5`：PASS。
- 候选 HEAD 已核对为 `1fb396777cce6274a44989755e6859fa2a3b2ed5`；报告写入前 tracked/staged 工作区为空。

## Conclusion

`1fb39677` 正确修复了 newline regression 的证据缺口：测试现在传入并验证一个字面 newline，而不是被 command substitution 删除后的空值。生产实现与此前批准的 shell 单路径处理、numeric patch、hosted copy/symlink、Python 3.11-3.14 identity/minor 及非 hosted 边界完全相同，批准结论继续成立。

SPEC_COMPLIANCE=APPROVED
CODE_QUALITY=APPROVED
