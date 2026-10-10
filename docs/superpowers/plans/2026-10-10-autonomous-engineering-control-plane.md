# 自治工程控制平面实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Survey 仓库建立可版本化、可恢复、可测试的自治工程控制平面，使 Codex 能在有限权限和明确终止条件下完成单个 Milestone。

**Architecture:** 使用 Python 3 标准库实现确定性 Harness，仓库内保存规则、Schema、质量门和保护路径，本地 `var/agent-harness/runs/` 保存原始运行状态。CLI 组合状态机、Git 防护、质量门、诊断脱敏和受控 `codex exec` 适配器；GitHub Actions 独立验证治理规则，但不自动合并或部署。

**Tech Stack:** Python 3.11+ 标准库、`unittest`、Git CLI、Codex CLI、GitHub Actions、现有 Docker/Node 22/Java 21 工具链。

**Spec:** `docs/superpowers/specs/2026-10-10-autonomous-engineering-control-plane-design.md`

**Status:** 已于 2026-10-10 获用户书面确认

## Global Constraints

- 根目录 `AGENTS.md` 是项目级强制规则入口；详细规则放在 `docs/agent/`，避免把易变命令重复注入每次会话。
- `docs/superpowers/specs/` 和 `docs/superpowers/plans/` 继续作为设计与实施计划的唯一事实源，不新增手工维护的平行 `Prompt.md`、`Plan.md` 或 `Documentation.md`。
- `GATE_MATRIX.yaml` 与 `PROTECTED_PATHS.yaml` 使用 JSON 语法书写；JSON 是 YAML 1.2 的合法子集，因此可由 Python 标准库 `json` 解析而不引入 PyYAML。
- 原始状态和日志只写入 `var/agent-harness/runs/` 并加入 `.gitignore`；提交到 `docs/agent/run-history/` 的内容必须先脱敏。
- 无人值守 Codex 固定使用独立 worktree、`--sandbox workspace-write`、`--approve-for-me`、`--strict-config`、`--json` 和 `--output-schema`。
- 禁止调用 `--dangerously-bypass-approvals-and-sandbox`、`--dangerously-bypass-hook-trust` 或任何等效无限权限选项。
- 同一失败指纹最多自动修复 3 次；单个 Milestone 最多 5 个失败修复周期；连续 2 轮无实质进展必须暂停。
- Harness 不执行 force push、自动 rebase、自动合并、覆盖标签或生产部署。
- 不记录令牌、密码、私钥、Cookie、Authorization 值、完整证书或敏感环境变量值。
- 所有功能以测试先行；每项实现必须运行任务内测试，整合后运行完整 Harness 测试和仓库现有相应质量门。

## Review Focus

- 路径包含 `..`、符号链接或大小写差异时，保护策略必须按仓库真实规范路径判定并拒绝逃逸；Task 3 的路径逃逸测试固定该行为。
- 进程在写 `state.json` 或 `events.jsonl` 中途终止时，恢复只能读取最后一个完整状态，不能接受半写文件；Task 2 的原子写入和事件截断测试固定该行为。
- Git HEAD 改变后，任何旧 Gate 成功证据都必须失效，即使文件 diff 看起来相同；Task 4 和 Task 6 的证据失效测试固定该行为。
- 日志中的 URL 凭据、JWT、Authorization、Cookie、PEM 私钥和自定义秘密环境变量必须同时脱敏；Task 5 的参数化测试固定该行为。
- Codex CLI 超时、退出非零、JSONL 损坏、最终输出不合 Schema 或会话 ID 缺失时必须安全暂停且不能重试越过预算；Task 7 的适配器和循环测试固定该行为。

## Dependency And Parallelism

```text
Task 1 -> Task 2
Task 2 -> Task 3, Task 4, Task 5   # 三项可由独立 agent 并行
Task 3 + Task 4 + Task 5 -> Task 6
Task 6 -> Task 7
Task 7 -> Task 8
```

Task 3、4、5 分别只拥有 `git_guard.py`、`gate_runner.py`、`diagnostics.py` 及其测试。并行 agent 不得修改其他任务文件；公共接口变更必须回到 Task 2 的状态契约统一处理。

---

### Task 1: Project rules, persistent memory, and policy contracts

**Files:**
- Create: `AGENTS.md`
- Create: `docs/agent/AUTONOMY.md`
- Create: `docs/agent/PROJECT_MEMORY.md`
- Create: `docs/agent/GATE_MATRIX.yaml`
- Create: `docs/agent/PROTECTED_PATHS.yaml`
- Create: `docs/agent/STATE_SCHEMA.json`
- Create: `docs/agent/run-history/README.md`
- Create: `tools/agent-harness/README.md`
- Create: `tools/agent-harness/agent_harness/__init__.py`
- Create: `tools/agent-harness/agent_harness/config.py`
- Create: `tools/agent-harness/tests/__init__.py`
- Create: `tools/agent-harness/tests/test_config.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: approved design and existing repository test commands.
- Produces: `GateDefinition`, `GateMatrix`, `PathRule`, `PolicyConfig`; `load_gate_matrix(path: Path) -> GateMatrix`; `load_protected_paths(path: Path) -> PolicyConfig`.

- [x] **Step 1: Write failing configuration tests**

Add tests named `test_loads_json_compatible_yaml_without_dependency`, `test_rejects_unknown_top_level_keys`, `test_rejects_shell_string_commands`, `test_gate_ids_are_unique`, and `test_path_rule_actions_are_closed_enum`.

Assert that commands are arrays of non-empty strings, timeouts are positive integers, allowed actions are exactly `deny`, `approval_required`, `review_required`, `generated`, duplicate IDs fail, and no PyYAML import is required.

- [x] **Step 2: Run the focused tests and verify failure**

Run: `python3 -m unittest discover -s tools/agent-harness/tests -p 'test_config.py' -v`

Expected: FAIL because `agent_harness.config` and policy files do not exist.

- [x] **Step 3: Implement the configuration model and parser**

In `config.py`, define immutable dataclasses and exact loaders from the Interfaces block. Reject unknown keys, relative repository escapes, empty path patterns, string-valued commands, duplicate IDs and unsupported actions with `ConfigError`.

- [x] **Step 4: Write the project rule and policy documents**

Keep `AGENTS.md` under 200 lines and link detailed procedures. Populate stable project facts in `PROJECT_MEMORY.md`, including source paths, current stack, existing quality gates and the rule that mutable runtime facts must be revalidated.

Configure initial Gate profiles for `admin-web`, `platform-java`, `publish-gateway`, `production-release`, `cross-stack-e2e`, `documentation`, and `agent-harness`. Configure protected categories for secrets, production/release, migrations/auth, generated files and normal review paths.

- [x] **Step 5: Ignore raw run state and verify configuration**

Add `/var/agent-harness/` to `.gitignore` and verify:

Run: `python3 -m unittest discover -s tools/agent-harness/tests -p 'test_config.py' -v`

Expected: all tests PASS and `git check-ignore var/agent-harness/runs/example/state.json` reports `.gitignore`.

- [x] **Step 6: Commit the rule contract**

```bash
git add AGENTS.md .gitignore docs/agent tools/agent-harness
git commit -m "feat: add autonomous engineering policy contract"
```

---

### Task 2: Atomic run state and event chain

**Files:**
- Create: `tools/agent-harness/agent_harness/state.py`
- Create: `tools/agent-harness/tests/test_state.py`
- Create: `tools/agent-harness/tests/fixtures/valid_state.json`

**Interfaces:**
- Consumes: `docs/agent/STATE_SCHEMA.json` and repository-relative `Path` values.
- Produces: `RunStatus`, `GateStatus`, `GateEvidence`, `AttemptState`, `RunState`; `sha256_file(path: Path) -> str`; `load_state(path: Path) -> RunState`; `save_state_atomic(path: Path, state: RunState) -> None`; `transition(state: RunState, target: RunStatus, reason: str) -> RunState`; `append_event(path: Path, event_type: str, payload: Mapping[str, object]) -> str`; `read_events(path: Path) -> list[dict[str, object]]`.

- [x] **Step 1: Write failing model and transition tests**

Add tests for exact states `planned`, `active`, `verifying`, `repairing`, `completed`, `paused`, `blocked`; allow only the transitions in the design; reject `active -> completed`; reject missing required fields and unknown schema versions.

- [x] **Step 2: Write failing atomicity and event-chain tests**

Add `test_atomic_save_preserves_previous_state_when_replace_fails`, `test_event_chain_detects_modified_middle_record`, and `test_event_reader_rejects_truncated_final_line` using temporary directories and patched filesystem calls.

- [x] **Step 3: Run the focused tests and verify failure**

Run: `python3 -m unittest tools/agent-harness/tests/test_state.py -v`

Expected: FAIL because `agent_harness.state` does not exist.

- [x] **Step 4: Implement immutable state types and transition validation**

Serialize timestamps as UTC RFC 3339 with `Z`. Preserve unknown runtime data only when the schema explicitly allows it; otherwise fail closed. A state transition returns a new dataclass instance and updates `updatedAt`.

- [x] **Step 5: Implement atomic state writes and chained events**

Write state to a same-directory temporary file, flush, `os.fsync`, then `os.replace`. Each event stores `previousDigest` and its own digest over canonical JSON. Reject any malformed or mismatched chain during read.

- [x] **Step 6: Verify and commit**

Run: `python3 -m unittest tools/agent-harness/tests/test_state.py -v`

Expected: all tests PASS.

```bash
git add tools/agent-harness/agent_harness/state.py tools/agent-harness/tests/test_state.py tools/agent-harness/tests/fixtures
git commit -m "feat: add atomic autonomous run state"
```

---

### Task 3: Git and protected-path guard

**Files:**
- Create: `tools/agent-harness/agent_harness/git_guard.py`
- Create: `tools/agent-harness/tests/test_git_guard.py`

**Interfaces:**
- Consumes: `PolicyConfig` from Task 1 and `RunState` from Task 2.
- Produces: `GitSnapshot`; `capture_snapshot(repo: Path) -> GitSnapshot`; `changed_paths(repo: Path, base: str, head: str | None = None) -> tuple[str, ...]`; `classify_paths(repo: Path, paths: Iterable[str], policy: PolicyConfig) -> tuple[PathDecision, ...]`; `validate_resume(snapshot: GitSnapshot, state: RunState) -> ResumeDecision`; `assert_worktree_isolated(repo: Path) -> None`.

- [x] **Step 1: Write failing temporary-repository tests**

Cover clean and dirty snapshots, branch and HEAD capture, detached HEAD rejection, untracked files, unrelated pre-existing changes, and a valid child commit produced by the same run.

- [x] **Step 2: Write failing path-boundary tests**

Add tests for `../` escape, absolute paths, symlink escape, normalized duplicate separators and case-sensitive path matching. Assert escaped or unresolved paths receive `deny` before any lower-priority rule.

- [x] **Step 3: Run the focused tests and verify failure**

Run: `python3 -m unittest tools/agent-harness/tests/test_git_guard.py -v`

Expected: FAIL because `agent_harness.git_guard` does not exist.

- [x] **Step 4: Implement Git queries without shell interpolation**

Use `subprocess.run([...], cwd=repo, text=True, capture_output=True, check=False)` with fixed argument arrays. Normalize porcelain-v2 output and return explicit errors for non-repositories, shared checkout use, detached HEAD and ambiguous ancestry.

- [x] **Step 5: Implement protected-path classification and resume decisions**

Evaluate canonical repository-relative paths with precedence `deny > approval_required > generated > review_required`. `validate_resume` must distinguish exact match, explainable descendant commit, dirty drift, branch drift and rewritten history.

- [x] **Step 6: Verify and commit**

Run: `python3 -m unittest tools/agent-harness/tests/test_git_guard.py -v`

Expected: all tests PASS.

```bash
git add tools/agent-harness/agent_harness/git_guard.py tools/agent-harness/tests/test_git_guard.py
git commit -m "feat: enforce autonomous git boundaries"
```

---

### Task 4: Quality-gate planner and evidence runner

**Files:**
- Create: `tools/agent-harness/agent_harness/gate_runner.py`
- Create: `tools/agent-harness/tests/test_gate_runner.py`

**Interfaces:**
- Consumes: `GateMatrix` from Task 1, `RunState` and `GateEvidence` from Task 2, changed repository paths from Task 3.
- Produces: `resolve_required_gates(matrix: GateMatrix, changed_paths: Iterable[str]) -> tuple[GateDefinition, ...]`; `run_gate(repo: Path, gate: GateDefinition, evidence_dir: Path, head_commit: str) -> GateEvidence`; `invalidate_stale_evidence(state: RunState, current_head: str) -> RunState`.

- [x] **Step 1: Write failing gate-resolution tests**

Assert that one path selects its profile, multiple areas produce a stable deduplicated union, unknown implementation paths fail closed, documentation-only changes select documentation checks, and a developer may add but not remove inferred Gate IDs.

- [x] **Step 2: Write failing execution tests**

Cover success, non-zero exit, timeout, missing executable, fixed working directory, stdout/stderr evidence files and argument-array execution. Assert no `shell=True` path exists.

- [x] **Step 3: Write failing evidence-invalidation test**

Create passed evidence for HEAD A, change to HEAD B, call `invalidate_stale_evidence`, and assert every code-sensitive Gate returns to `pending` while retaining the old evidence only as historical data.

- [x] **Step 4: Run the focused tests and verify failure**

Run: `python3 -m unittest tools/agent-harness/tests/test_gate_runner.py -v`

Expected: FAIL because `agent_harness.gate_runner` does not exist.

- [x] **Step 5: Implement gate planning and execution**

Run commands with fixed arrays, configured relative `cwd`, `timeout`, controlled environment inheritance and text output. Save metadata separately from logs; never infer success from output text when the exit code failed.

- [x] **Step 6: Verify and commit**

Run: `python3 -m unittest tools/agent-harness/tests/test_gate_runner.py -v`

Expected: all tests PASS.

```bash
git add tools/agent-harness/agent_harness/gate_runner.py tools/agent-harness/tests/test_gate_runner.py
git commit -m "feat: add scoped autonomous quality gates"
```

---

### Task 5: Failure fingerprinting, retry budget, and diagnostics redaction

**Files:**
- Create: `tools/agent-harness/agent_harness/diagnostics.py`
- Create: `tools/agent-harness/tests/test_diagnostics.py`

**Interfaces:**
- Consumes: failed `GateEvidence`, `RunState`, changed paths and decision summaries.
- Produces: `normalize_failure(text: str) -> str`; `failure_fingerprint(exit_code: int, stdout: str, stderr: str) -> str`; `record_failure(state: RunState, fingerprint: str, diff_digest: str) -> RetryDecision`; `redact_text(text: str, secret_names: Iterable[str] = ()) -> str`; `render_diagnostics(state: RunState, evidence: Iterable[GateEvidence], output: Path) -> None`.

- [x] **Step 1: Write failing fingerprint and budget tests**

Assert timestamps, ANSI colors, random ports and temporary paths do not change a fingerprint. Assert attempt 3 for one fingerprint pauses, total failed cycle 5 pauses, and two identical diff/fingerprint/gate-result cycles pause as `no_progress`.

- [x] **Step 2: Write failing redaction tests**

Use fake values to cover URL userinfo, bearer/basic Authorization, Cookie, JWT-shaped strings, GitHub/OpenAI-style tokens, PEM private keys, `PASSWORD=`, `TOKEN=`, and caller-supplied secret names. Assert labels remain useful while values become `[REDACTED]`.

- [x] **Step 3: Write failing diagnostics-content test**

Assert a paused report contains plan, Milestone, branch, HEAD, changed paths, failed Gate, fingerprint, attempts, stop reason and exact resume command, but contains none of the fake secrets or raw full log.

- [x] **Step 4: Run the focused tests and verify failure**

Run: `python3 -m unittest tools/agent-harness/tests/test_diagnostics.py -v`

Expected: FAIL because `agent_harness.diagnostics` does not exist.

- [x] **Step 5: Implement normalization, budget decisions and redaction**

Use compiled regular expressions with bounded input size. Fingerprint normalized first-error context plus exit code using SHA-256. Return explicit decision codes `repair_allowed`, `same_failure_limit`, `total_attempt_limit`, and `no_progress`.

- [x] **Step 6: Verify and commit**

Run: `python3 -m unittest tools/agent-harness/tests/test_diagnostics.py -v`

Expected: all tests PASS.

```bash
git add tools/agent-harness/agent_harness/diagnostics.py tools/agent-harness/tests/test_diagnostics.py
git commit -m "feat: add autonomous failure escalation"
```

---

### Task 6: Harness CLI, doctor, resume, and finalize

**Files:**
- Create: `tools/agent-harness/agent_harness/cli.py`
- Create: `tools/agent-harness/agent_harness/doctor.py`
- Create: `tools/agent-harness/agent_harness/run_service.py`
- Create: `tools/agent-harness/tests/test_cli.py`
- Create: `tools/agent-harness/tests/test_doctor.py`
- Create: `tools/agent-harness/tests/test_run_service.py`
- Create: `scripts/agent-harness`

**Interfaces:**
- Consumes: Tasks 1-5 public interfaces.
- Produces: `main(argv: Sequence[str] | None = None) -> int`; `run_doctor(repo: Path) -> DoctorReport`; `RunService.init(plan: Path, milestone: str) -> RunState`; `RunService.next_action(run_id: str) -> NextAction`; `RunService.record_decision(run_id: str, decision_type: str, summary: str) -> RunState`; `RunService.pause(run_id: str, reason: str) -> Path`; `RunService.resume(run_id: str) -> RunState`; `RunService.run_gates(run_id: str, extra_gate_ids: Sequence[str]) -> RunState`; `RunService.finalize(run_id: str) -> Path`.

- [x] **Step 1: Write failing CLI contract tests**

Cover `doctor`, `init`, `status --json`, `next`, `gate`, `record-decision`, `pause`, `resume`, and `finalize`; assert stable exit codes `0` success, `2` invalid input/config, `3` policy refusal, `4` Gate failure, `5` paused/blocked. `next` only reports the current actionable operation and must never mutate state or advance a Milestone.

- [x] **Step 2: Write failing doctor tests**

Patch subprocess discovery for Git, Docker, Node 22, Java 21, Python 3.11+, Codex and disk availability. Assert missing tools are reported without cleanup or mutation, and the JSON report separates `ok`, `warning`, and `error` checks.

- [x] **Step 3: Write failing lifecycle integration tests**

In a temporary Git worktree, test plan binding and SHA-256, initialization refusal on dirty/unapproved plan, Gate success, evidence invalidation after HEAD change, pause/resume, atomic completion, and generation of a sanitized `docs/agent/run-history/<run-id>.md` for both paused and completed terminal records.

- [x] **Step 4: Run the focused tests and verify failure**

Run: `python3 -m unittest tools/agent-harness/tests/test_cli.py tools/agent-harness/tests/test_doctor.py tools/agent-harness/tests/test_run_service.py -v`

Expected: FAIL because the CLI and service do not exist.

- [x] **Step 5: Implement doctor and orchestration service**

`init` accepts only a plan containing an approved-status marker and a matching Milestone heading. `resume` re-runs doctor and Git validation. `finalize` requires all inferred Gates passed for the current HEAD and no unresolved approval-required path.

- [x] **Step 6: Implement CLI and portable wrapper**

The wrapper resolves repository root and prepends `tools/agent-harness` to `PYTHONPATH`; it must work from any subdirectory. Human output is Chinese and concise; `--json` emits one valid JSON object with no ANSI codes.

- [x] **Step 7: Verify and commit**

Run: `python3 -m unittest discover -s tools/agent-harness/tests -v`

Run: `scripts/agent-harness doctor --json`

Expected: tests PASS; doctor emits valid JSON and performs no mutations.

```bash
git add scripts/agent-harness tools/agent-harness
git commit -m "feat: add autonomous harness lifecycle cli"
```

---

### Task 7: Controlled Codex adapter and bounded autonomous loop

**Files:**
- Create: `tools/agent-harness/agent_harness/codex_adapter.py`
- Create: `tools/agent-harness/agent_harness/codex_response.schema.json`
- Create: `tools/agent-harness/tests/test_codex_adapter.py`
- Create: `tools/agent-harness/tests/test_autonomous_loop.py`
- Modify: `tools/agent-harness/agent_harness/run_service.py`
- Modify: `tools/agent-harness/agent_harness/cli.py`

**Interfaces:**
- Consumes: `RunService`, retry decisions and diagnostics from Tasks 5-6.
- Produces: `CodexResult`; `build_codex_command(repo: Path, schema: Path, prompt: str, resume_session: str | None = None) -> tuple[str, ...]`; `run_codex(command: Sequence[str], timeout_seconds: int, event_log: Path) -> CodexResult`; `RunService.run_autonomous(run_id: str, max_cycles: int) -> RunState`.

- [x] **Step 1: Write failing command-policy tests**

Assert the command contains `exec`, `--sandbox workspace-write`, `--approve-for-me`, `--strict-config`, `--json`, `--output-schema` and `--cd <worktree>`; assert it never contains either dangerous bypass flag, `danger-full-access`, `--add-dir`, arbitrary config overrides or secrets.

- [x] **Step 2: Write failing JSONL and schema tests**

Cover a valid completed event stream, non-zero exit, timeout, malformed JSONL, oversized event, missing session ID, missing final response and final response that violates the response schema. Every invalid case returns a typed failure without throwing raw log contents into diagnostics.

- [x] **Step 3: Write failing bounded-loop tests**

Use a fake adapter to verify: successful code change triggers Gate execution; Gate failure produces a focused repair prompt; same fingerprint stops on attempt 3; total failures stop on 5; two no-progress cycles stop; approval-required path stops immediately; completion is impossible until current-HEAD Gates pass.

- [x] **Step 4: Run the focused tests and verify failure**

Run: `python3 -m unittest tools/agent-harness/tests/test_codex_adapter.py tools/agent-harness/tests/test_autonomous_loop.py -v`

Expected: FAIL because the adapter and loop do not exist.

- [x] **Step 5: Implement the output schema and subprocess adapter**

The final response contains exactly `status`, `summary`, `changedPaths`, `testsRequested`, `needsHuman`, and `sessionId`. Save raw JSONL only under the run directory; expose only typed metadata to the state service.

- [x] **Step 6: Implement the bounded loop**

Each cycle re-reads state and Git, validates scope, invokes Codex, runs inferred Gates, fingerprints failures, records an event and either repairs, completes or pauses. Prompt content is bounded to the Milestone, approved file paths, current failure summary and remaining attempt count.

- [x] **Step 7: Verify with fake adapter and guarded local smoke test**

Run: `python3 -m unittest tools/agent-harness/tests/test_codex_adapter.py tools/agent-harness/tests/test_autonomous_loop.py -v`

Run: `scripts/agent-harness run-codex --help`

Expected: tests PASS; help exits 0 without invoking Codex. A real Codex smoke run is deferred to Task 8's disposable worktree drill.

- [x] **Step 8: Commit**

```bash
git add tools/agent-harness/agent_harness tools/agent-harness/tests
git commit -m "feat: add bounded codex autonomous loop"
```

---

### Task 8: Governance CI, fault-injection drills, and delivery synchronization

**Files:**
- Create: `.github/workflows/agent-governance.yml`
- Create: `tools/agent-harness/tests/test_governance.py`
- Create: `tools/agent-harness/tests/test_fault_injection.py`
- Create: `tools/agent-harness/drills/run_drills.py`
- Modify: `tools/agent-harness/README.md`
- Modify: `docs/agent/PROJECT_MEMORY.md`
- Modify: `platform/README.md`
- Modify: `platform/docs/p2/progress.md`
- Modify: `docs/superpowers/plans/2026-10-10-autonomous-engineering-control-plane.md`

**Interfaces:**
- Consumes: the complete Harness from Tasks 1-7 and existing repository quality workflow.
- Produces: GitHub `agent-governance` checks, reproducible local drills, final run-history evidence and synchronized project documentation.

- [x] **Step 1: Write failing governance-policy tests**

Assert the workflow uses Ubuntu, Python 3.11, read-only default permissions, pinned action commits, Harness unit tests, config validation, forbidden-option scan and plan/memory drift checks. Assert it has no write permission, deployment, release creation or auto-merge step.

- [x] **Step 2: Write failing fault-injection tests**

Cover process termination before and after atomic replace, truncated events, changed plan digest, rewritten Git history, missing Docker/Node/Java, fake-secret diagnostics, repeated Gate failure and Codex timeout.

- [x] **Step 3: Implement the governance workflow and drill runner**

The drill runner operates only in temporary directories, uses fake commands by default, requires `--allow-real-codex` for one disposable worktree smoke run, and refuses a repository containing production secret paths.

- [ ] **Step 4: Run all Harness verification**

Run: `scripts/agent-harness --python tools/agent-harness/run_tests.py --start-directory tools/agent-harness/tests --pattern 'test_*.py'`

Run: `scripts/agent-harness --python tools/agent-harness/drills/run_drills.py`

Run: `scripts/agent-harness doctor --json`

Expected: all tests and fake drills PASS; doctor produces a valid report. Record exact counts and environment warnings in the final run history.

Local evidence may include typed Linux integration skips when Docker is unavailable. This step remains open until a bound GitHub Ubuntu 24.04
run proves both jobs use Python 3.11 and complete with zero skips. Local exact counts are recorded in the Task 8 report.

- [x] **Step 5: Run the disposable real-Codex smoke drill**

Create a temporary worktree from the current branch and select a fixture-only Milestone whose permitted change is adding one expected line to a disposable test fixture. Run exactly one Codex cycle with `--allow-real-codex`, then verify changed paths, Gate evidence, run history and worktree cleanup instructions.

Expected: Codex runs in `workspace-write`, touches only the permitted fixture, and either completes with passing Gate evidence or pauses with sanitized diagnostics. Either outcome must leave the primary worktree unchanged.

Evidence (2026-10-10): exactly one real Codex cycle ran and returned `paused` with 0 changed paths and 0 passed Gates.
The temporary run-history existed and passed the sanitized-history check before cleanup; the disposable worktree and branch were removed,
and the primary worktree identity was unchanged. The then-current summary omitted the sanitized stop classification; the runner now retains it,
but the smoke was not repeated because this Task permits exactly one real cycle.

- [x] **Step 6: Run repository integration gates**

Run: `scripts/agent-harness --python -m unittest discover -s platform/tools/productization -p 'test_render_capabilities.py'`

Run: `scripts/agent-harness --python platform/tools/productization/render_capabilities.py --check`

Run: `git diff --check`

Expected: all commands PASS.

- [x] **Step 7: Perform independent review and resolve findings**

Dispatch one reviewer for security and state recovery, one for developer ergonomics and CI, and one whole-branch reviewer. Re-run all Harness tests after accepted fixes and record rejected findings with technical reasons in the run summary.

Evidence (2026-10-10): security, DX/CI and whole-branch rereview reports were received independently. Both review rounds' deduplicated
findings were reproduced with failing tests and resolved; the final focused and full verification evidence is recorded in the Task 8 report.

- [ ] **Step 8: Synchronize documentation and mark the plan complete**

Document install/use/resume/escalation commands, final test evidence, remaining limitations and the first eligible real product Milestone. Update this plan's checkboxes only from actual evidence and add the implementation commit references.

Local documentation is synchronized to the round-2 implementation commit `57f91048`, but this step remains open because the plan cannot
be marked complete before the bound GitHub run and Obsidian synchronization. The first candidate product Milestone is Phase C-1's
`820–1179px` two-column and inspector shell, subject to host synchronization and a fresh approved file scope.

- [ ] **Step 9: Commit, push, and update Obsidian**

```bash
git add .github/workflows/agent-governance.yml tools/agent-harness docs/agent platform/README.md platform/docs/p2/progress.md docs/superpowers/plans/2026-10-10-autonomous-engineering-control-plane.md
git commit -m "ci: enforce autonomous engineering governance"
git push
```

Update the Survey project overview/progress note with date, branch, commit range, test evidence, completed scope, known limitations and the next product-development Milestone. Do not record credentials or raw run logs.

Partially completed: Task 8 changes are committed locally. Per the controller instruction, this implementer does not push and does not update
Obsidian; those two delivery actions remain open, so Task 8 and the plan remain in progress.

## Final Acceptance

- [x] Root rules, permanent memory, machine policy and raw runtime separation are present and documented.
- [x] State writes, event chains, Git drift checks, protected paths, Gate planning, evidence invalidation, retry limits and redaction pass automated tests.
- [x] CLI supports `doctor/init/status/next/gate/record-decision/pause/resume/run-codex/finalize` with stable exit codes.
- [x] Real Codex execution is bounded to an isolated worktree and cannot select dangerous bypass flags.
- [x] Success, repeated failure, no progress, interrupted write, plan drift and CLI timeout drills all produce expected terminal states.
- [ ] GitHub governance runs independently and cannot merge, release or deploy.
- [ ] Repository docs, GitHub branch and Obsidian project status agree on the delivered scope and remaining product work.
