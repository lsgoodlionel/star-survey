# Token-Efficient Engineering Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task by task. Keep Wave 2 product code untouched until this control-plane plan reaches its stated integration gate.

**Goal:** 在不降低测试、双库验证、安全门禁和生产验收标准的前提下，把重复上下文、完整日志、重复 Gate 和重复 Agent 探索从模型上下文中移出，形成可复用的 Context Capsule、Gate Evidence、Token Telemetry 与 Obsidian 投影闭环。

**Architecture:** 以现有 `tools/agent-harness` 为唯一执行控制面，继续只使用 Python 3.11 标准库。所有测试仍由本地进程、Docker 或 GitHub Actions 执行；模型只接收有严格字节预算的结构化摘要。仓库与 Harness 是事实源，Obsidian 和全局记忆是可校验投影，不反向覆盖代码或状态。

**Tech Stack:** Python 3.11 standard library, JSON Schema subset, Git, GitHub Actions, Docker Compose, unittest, Markdown marker blocks

**Status:** 设计已于 2026-10-12 确认；本计划待执行方式确认后激活。

---

## 1. Execution Contract

### 1.1 Worktree and scope

- Repository: `/Users/lionel/Develop/survey/.worktrees/admin-web`
- Branch: `feat/admin-product-alignment`
- Source design: `docs/superpowers/specs/2026-10-12-token-efficient-engineering-design.md`
- Existing active product plan remains `docs/superpowers/plans/2026-10-11-template-branding-wave2.md` until this plan passes Task 10.
- Preserve every pre-existing staged, unstaged, and untracked Wave 2 file. Never use broad `git add -A` or `git commit -a`.
- Each task must commit only its listed files after its own focused tests pass.

### 1.2 Hard constraints

- Do not introduce third-party Python packages.
- Do not upload prompts, source code, raw logs, session JSONL, secrets, or absolute local paths to GitHub artifacts.
- Do not reduce or skip required tests to meet a Token target.
- Production, security, migration, release, browser, dual-database, backup, restore, and rollback gates remain bound to exact Git HEAD.
- A cache miss, malformed summary, unknown schema version, uncertain runtime identity, or untrusted GitHub result must fail closed and run the gate normally.
- One implementation Agent owns one task at a time. Use one final independent reviewer after Task 10; do not create reviewer Agents after every task.
- Full logs stay on disk. They enter a model context only through an explicit bounded diagnostic escalation.

### 1.3 Required loop for every task

1. Read this task and only the directly referenced files.
2. Write the failing focused test first.
3. Run that focused test locally without sending its output to another model.
4. Implement the minimum production change.
5. Re-run the focused test and the nearest regression suite.
6. Record only command, exit code, duration, counts, evidence path, and bounded failure summary.
7. Commit only the task files with a path-scoped `git add`.

## 2. Resource Forecast and Acceptance Budget

### 2.1 Historical baseline

Measured from local Codex session metadata for 2026-10-09 14:22 through the 2026-10-12 pause:

| Metric | Observed |
| --- | ---: |
| Raw model throughput | about 1.487B tokens |
| Cached input | about 1.455B tokens |
| Cache hit ratio | about 98.1% |
| Active incremental tokens | about 32.98M tokens |
| Project-related subagent sessions | 91 |
| Committed added lines | 47,840 |
| Current candidate startup context | over 200 KiB |

`active_tokens = input_tokens - cached_input_tokens + output_tokens + reasoning_tokens`.
Raw throughput is useful for context duplication analysis; active tokens are the cost and reasoning comparison metric. Account quota percentages must not be converted to an invented absolute Token cap.

### 2.2 Remaining-development forecast

| Scenario | Remaining active tokens | Remaining raw throughput | Relative effect |
| --- | ---: | ---: | --- |
| Continue prior workflow unchanged | 56M-98M | 2.5B-4.4B | Baseline |
| Only localize/offload tests | 40M-70M | 1.4B-3.4B | Active down about 17%-29% |
| This full plan | 32M-58M | 0.5B-1.2B | Active down about 35%-45%; raw down about 73%-80% |

Compared with the unchanged workflow, the full plan is expected to save about 24M-40M active tokens and 2.0B-3.2B raw tokens. Compared with test-only optimization, it should save a further 8M-12M active tokens because Agent startup and repeated context discovery become deterministic.

Forecast assumptions:

- Stable shared prefix is at most 12 KiB.
- Per-task delta is at most 8 KiB.
- Default failure diagnostic is at most 4 KiB.
- First-turn Context Capsule is at most 24 KiB, about 88% smaller than the current candidate context.
- Subagent startup active tokens fall at least 70%.
- Tests execute locally or in GitHub and contribute model tokens only when a bounded failure summary requires reasoning.
- At the observed 8M-12M active tokens per high-intensity development day, remaining model work is about 3-7 intensive days. Code plus local verification remains about 7-12 calendar days; full GitHub and production acceptance remains about 12-20 calendar days because external builds, deployment, browser checks, and human review cannot be compressed by Token policy.

### 2.3 Quantitative acceptance gates

- `context_capsule.total_bytes <= 24576`.
- Stable prefix `<= 12288` bytes and byte-identical for Agents sharing the same policy version.
- Task delta `<= 8192` bytes.
- Default diagnostic `<= 4096` bytes with one root cause and at most three related errors.
- A passing Gate summary contains no stdout/stderr payload.
- Ten simultaneous requests for one evidence key launch exactly one Gate process.
- Token report contains no prompt/message/source/log body fields.
- Benchmark fixture demonstrates at least 80% raw context reduction and at least 70% Agent startup active-token reduction against the checked-in baseline fixture.

## 3. Task 1: Version Contracts and Backward-Compatible State

**Files:**

- Modify: `tools/agent-harness/agent_harness/config.py`
- Modify: `tools/agent-harness/agent_harness/state.py`
- Modify: `tools/agent-harness/tests/test_config.py`
- Modify: `tools/agent-harness/tests/test_state.py`
- Modify: `docs/agent/GATE_MATRIX.yaml`
- Modify: `docs/agent/STATE_SCHEMA.json`
- Modify: `docs/agent/HARNESS_HOST.json`
- Create: `docs/agent/GATE_SUMMARY_SCHEMA.json`
- Create: `docs/agent/CONTEXT_CAPSULE_SCHEMA.json`
- Create: `docs/agent/TOKEN_REPORT_SCHEMA.json`
- Create: `docs/agent/MEMORY_PROJECTIONS.json`

**Step 1: Add failing contract tests**

Cover these exact rules:

- `GateDefinition` accepts `execution_tier`, `reuse_policy`, `input_patterns`, and `summary_format` only from documented enums.
- Legacy version 1 state remains readable and normalizes in memory.
- Newly written state uses version 2 and includes summary/evidence metadata.
- Unknown versions and unknown keys fail closed.
- Every new schema rejects absolute paths, traversal, unbounded text fields, and unsupported source types.

**Step 2: Run focused tests and confirm RED**

```bash
scripts/agent-harness --python -m unittest tests.test_config tests.test_state
```

**Step 3: Implement the contracts**

Extend `GateDefinition` with:

```python
execution_tier: str
reuse_policy: str
input_patterns: tuple[str, ...]
summary_format: str
```

Extend `GateEvidence` with optional `summary_path`, `evidence_key`, `source`, `reused`, and `diagnostic_bytes`. Read legacy v1 records with safe defaults; write only v2. Keep configuration parsing strict and immutable.

**Step 4: Verify GREEN and schema compatibility**

```bash
scripts/agent-harness --python -m unittest tests.test_config tests.test_state tests.test_governance
```

**Step 5: Commit**

```bash
git add tools/agent-harness/agent_harness/config.py tools/agent-harness/agent_harness/state.py tools/agent-harness/tests/test_config.py tools/agent-harness/tests/test_state.py docs/agent/GATE_MATRIX.yaml docs/agent/STATE_SCHEMA.json docs/agent/HARNESS_HOST.json docs/agent/GATE_SUMMARY_SCHEMA.json docs/agent/CONTEXT_CAPSULE_SCHEMA.json docs/agent/TOKEN_REPORT_SCHEMA.json docs/agent/MEMORY_PROJECTIONS.json
git commit -m "feat(harness): version token efficiency contracts"
```

## 4. Task 2: Deterministic Gate Summaries

**Files:**

- Create: `tools/agent-harness/agent_harness/gate_summary.py`
- Create: `tools/agent-harness/tests/test_gate_summary.py`
- Modify: `tools/agent-harness/agent_harness/gate_runner.py`
- Modify: `tools/agent-harness/agent_harness/diagnostics.py`
- Modify: `tools/agent-harness/tests/test_gate_runner.py`
- Modify: `tools/agent-harness/tests/test_diagnostics.py`

**Step 1: Write failing summary tests**

Define and test:

```python
@dataclass(frozen=True)
class GateSummary:
    schema_version: int
    gate_id: str
    status: str
    exit_code: int | None
    duration_ms: int
    counts: Mapping[str, int]
    evidence_key: str | None
    head_commit: str
    diagnostic: str | None

def build_gate_summary(repo: Path, gate: GateDefinition,
                       evidence: GateEvidence, *,
                       max_diagnostic_bytes: int = 4096) -> GateSummary: ...

def write_gate_summary_atomic(path: Path, summary: GateSummary) -> None: ...
```

Passing summaries must contain no raw output. Failure summaries redact secrets, retain one root cause plus at most three related errors, and remain within the byte cap even for multibyte UTF-8.

**Step 2: Confirm RED**

```bash
scripts/agent-harness --python -m unittest tests.test_gate_summary tests.test_diagnostics
```

**Step 3: Implement deterministic parsers**

Support `generic`, `unittest`, `surefire`, `vitest`, and `playwright` formats with conservative count extraction. Store `summary.json` beside raw evidence and write atomically. A parser uncertainty must set counts to unknown/zero fields without inventing success.

**Step 4: Verify**

```bash
scripts/agent-harness --python -m unittest tests.test_gate_summary tests.test_diagnostics tests.test_gate_runner
```

**Step 5: Commit**

```bash
git add tools/agent-harness/agent_harness/gate_summary.py tools/agent-harness/agent_harness/gate_runner.py tools/agent-harness/agent_harness/diagnostics.py tools/agent-harness/tests/test_gate_summary.py tools/agent-harness/tests/test_gate_runner.py tools/agent-harness/tests/test_diagnostics.py
git commit -m "feat(harness): emit bounded gate summaries"
```

## 5. Task 3: Evidence Keys and Selective Reuse

**Files:**

- Create: `tools/agent-harness/agent_harness/evidence_cache.py`
- Create: `tools/agent-harness/tests/test_evidence_cache.py`
- Modify: `tools/agent-harness/agent_harness/gate_runner.py`
- Modify: `tools/agent-harness/tests/test_gate_runner.py`

**Step 1: Write failing cache tests**

Cover content hashing, runtime identity, executable bits, symlink metadata, missing inputs, dirty files, malformed cache entries, and strict HEAD-only gates.

**Step 2: Confirm RED**

```bash
scripts/agent-harness --python -m unittest tests.test_evidence_cache
```

**Step 3: Implement safe EvidenceKey APIs**

```python
@dataclass(frozen=True)
class RuntimeIdentity:
    os_name: str
    architecture: str
    database: str | None
    tool_versions: Mapping[str, str]

def compute_evidence_key(repo: Path, gate: GateDefinition,
                         runtime: RuntimeIdentity) -> str: ...

def lookup_cached_evidence(repo: Path, key: str, gate: GateDefinition,
                           head_commit: str) -> GateEvidence | None: ...

def publish_cached_evidence(repo: Path, key: str,
                            evidence: GateEvidence) -> None: ...
```

Permit content reuse only for deterministic lightweight gates explicitly marked `content`. Force `head` for security, migration, production, release, browser, backup/restore, and dual-database gates regardless of caller input.

**Step 4: Verify**

```bash
scripts/agent-harness --python -m unittest tests.test_evidence_cache tests.test_gate_runner tests.test_state
```

**Step 5: Commit**

```bash
git add tools/agent-harness/agent_harness/evidence_cache.py tools/agent-harness/agent_harness/gate_runner.py tools/agent-harness/tests/test_evidence_cache.py tools/agent-harness/tests/test_gate_runner.py
git commit -m "feat(harness): reuse trusted gate evidence"
```

## 6. Task 4: Gate Leases and Concurrent Deduplication

**Files:**

- Create: `tools/agent-harness/agent_harness/gate_lease.py`
- Create: `tools/agent-harness/tests/test_gate_lease.py`
- Modify: `tools/agent-harness/agent_harness/gate_runner.py`
- Modify: `tools/agent-harness/agent_harness/run_service.py`
- Modify: `tools/agent-harness/tests/test_run_service.py`

**Step 1: Write failing concurrency tests**

Use temporary directories and real subprocess counters. Ten concurrent callers for one key must execute once, consume the same summary, and not deadlock. Cover expired leases, dead owners, interrupted atomic writes, and a completed evidence race.

**Step 2: Confirm RED**

```bash
scripts/agent-harness --python -m unittest tests.test_gate_lease
```

**Step 3: Implement leases**

```python
def acquire_gate_lease(cache_root: Path, key: str, owner: str,
                       ttl_seconds: int) -> GateLease: ...

def wait_for_gate_summary(cache_root: Path, key: str,
                          timeout_seconds: int) -> GateSummary: ...
```

Use atomic directory creation and atomic rename only. A stale lease may be reclaimed only after expiry plus missing valid evidence; same-host PID checks are advisory, not authoritative.

**Step 4: Verify**

```bash
scripts/agent-harness --python -m unittest tests.test_gate_lease tests.test_gate_runner tests.test_run_service
```

**Step 5: Commit**

```bash
git add tools/agent-harness/agent_harness/gate_lease.py tools/agent-harness/agent_harness/gate_runner.py tools/agent-harness/agent_harness/run_service.py tools/agent-harness/tests/test_gate_lease.py tools/agent-harness/tests/test_run_service.py
git commit -m "feat(harness): deduplicate concurrent gates"
```

## 7. Task 5: Context Manifest and Context Capsule

**Files:**

- Create: `tools/agent-harness/agent_harness/context_capsule.py`
- Create: `tools/agent-harness/tests/test_context_capsule.py`
- Modify: `tools/agent-harness/agent_harness/codex_adapter.py`
- Modify: `tools/agent-harness/agent_harness/run_service.py`
- Modify: `tools/agent-harness/tests/test_codex_adapter.py`
- Modify: `tools/agent-harness/tests/test_run_service.py`
- Modify: `docs/agent/codex_response.schema.json`

**Step 1: Write failing budget and provenance tests**

Test deterministic ordering, section-range extraction, digest verification, UTF-8 byte accounting, path traversal rejection, unchanged stable-prefix bytes, budget overflow failure, and `capsuleDigest` response binding.

**Step 2: Confirm RED**

```bash
scripts/agent-harness --python -m unittest tests.test_context_capsule tests.test_codex_adapter
```

**Step 3: Implement capsule generation**

```python
@dataclass(frozen=True)
class ContextSource:
    path: str
    start_line: int
    end_line: int
    digest: str
    byte_count: int
    reason: str

@dataclass(frozen=True)
class ContextManifest: ...

@dataclass(frozen=True)
class ContextCapsule: ...

def extract_markdown_section(path: Path, heading: str) -> str: ...
def build_context_capsule(repo: Path, state: RunState, task: str,
                          diagnostic: str | None = None) -> ContextCapsule: ...
def write_context_capsule_atomic(path: Path,
                                 capsule: ContextCapsule) -> None: ...
```

Place stable policy first and volatile run/task fields last. Reject output over 24 KiB instead of silently truncating mandatory constraints. `codex_adapter` must pass capsule content, not whole plans or logs, and require a matching `capsuleDigest` in the structured response.

**Step 4: Verify**

```bash
scripts/agent-harness --python -m unittest tests.test_context_capsule tests.test_codex_adapter tests.test_run_service
```

**Step 5: Commit**

```bash
git add tools/agent-harness/agent_harness/context_capsule.py tools/agent-harness/agent_harness/codex_adapter.py tools/agent-harness/agent_harness/run_service.py tools/agent-harness/tests/test_context_capsule.py tools/agent-harness/tests/test_codex_adapter.py tools/agent-harness/tests/test_run_service.py docs/agent/codex_response.schema.json
git commit -m "feat(harness): build bounded context capsules"
```

## 8. Task 6: Privacy-Safe Token Telemetry

**Files:**

- Create: `tools/agent-harness/agent_harness/token_report.py`
- Create: `tools/agent-harness/tests/test_token_report.py`
- Modify: `tools/agent-harness/agent_harness/cli.py`
- Modify: `tools/agent-harness/tests/test_cli.py`

**Step 1: Write failing aggregation and privacy tests**

Fixtures must include cumulative main-session counters, final subagent counters, guardian/reviewer sessions, malformed JSONL, unrelated projects, missing cache values, and fake secrets/message payloads. Assert that output contains only numeric usage, timestamps, model/session category, and project/run identifiers.

**Step 2: Confirm RED**

```bash
scripts/agent-harness --python -m unittest tests.test_token_report
```

**Step 3: Implement telemetry**

```python
@dataclass(frozen=True)
class TokenUsage:
    raw_tokens: int
    cached_input_tokens: int
    active_tokens: int
    output_tokens: int
    reasoning_tokens: int

def collect_token_usage(session_roots: tuple[Path, ...], *,
                        since: datetime, until: datetime,
                        project_root: Path) -> TokenReport: ...
```

Parse only trusted numeric metadata event types. Compute main-session usage by cumulative deltas and each subagent from its final cumulative counter. Exclude guardian/review sessions from implementation totals but report them separately. Add `token-report --since --until --format json|text`; never emit source JSONL records.

**Step 4: Verify**

```bash
scripts/agent-harness --python -m unittest tests.test_token_report tests.test_cli
```

**Step 5: Commit**

```bash
git add tools/agent-harness/agent_harness/token_report.py tools/agent-harness/agent_harness/cli.py tools/agent-harness/tests/test_token_report.py tools/agent-harness/tests/test_cli.py
git commit -m "feat(harness): report privacy-safe token usage"
```

## 9. Task 7: Obsidian and Local-Memory Projection

**Files:**

- Create: `tools/agent-harness/agent_harness/memory_projection.py`
- Create: `tools/agent-harness/tests/test_memory_projection.py`
- Modify: `tools/agent-harness/agent_harness/cli.py`
- Modify: `tools/agent-harness/tests/test_cli.py`
- Modify: `docs/agent/MEMORY_PROJECTIONS.json`
- Modify: `docs/agent/HARNESS_HOST.json`
- Runtime-only ignored file: `var/agent-harness/host-memory.json`

**Step 1: Write failing projection tests**

Cover marker-bounded replacement, manual text preservation, idempotence, digest conflict detection, unavailable vault journaling, no absolute path in committed files, and a later successful replay.

**Step 2: Confirm RED**

```bash
scripts/agent-harness --python -m unittest tests.test_memory_projection
```

**Step 3: Implement one-way verified projection**

```python
@dataclass(frozen=True)
class ProjectionConfig: ...

def render_projection(state: RunState, delivery: Mapping[str, object],
                      token_report: TokenReport) -> Projection: ...
def plan_memory_sync(config: ProjectionConfig,
                     projection: Projection) -> MemorySyncPlan: ...
def apply_memory_sync(plan: MemorySyncPlan) -> MemorySyncResult: ...
```

Add CLI commands:

```text
configure-memory --vault <absolute-local-path>
memory-status
sync-memory --run-id <uuid>
```

The committed config contains only relative note paths and marker names. The local ignored host file stores the vault root. Update only machine-owned blocks in the existing project overview/progress notes. When the prior machine block digest differs unexpectedly, stop and report conflict rather than overwrite human edits.

**Step 4: Verify**

```bash
scripts/agent-harness --python -m unittest tests.test_memory_projection tests.test_cli
```

**Step 5: Commit**

```bash
git add tools/agent-harness/agent_harness/memory_projection.py tools/agent-harness/agent_harness/cli.py tools/agent-harness/tests/test_memory_projection.py tools/agent-harness/tests/test_cli.py docs/agent/MEMORY_PROJECTIONS.json docs/agent/HARNESS_HOST.json
git commit -m "feat(harness): project verified state to obsidian"
```

## 10. Task 8: GitHub Summaries and Trusted Evidence Import

**Files:**

- Create: `tools/agent-harness/agent_harness/github_evidence.py`
- Create: `tools/agent-harness/tests/test_github_evidence.py`
- Modify: `.github/workflows/platform-quality.yml`
- Modify: `.github/workflows/agent-governance.yml`
- Modify: `tools/agent-harness/tests/test_governance.py`
- Modify: `tools/agent-harness/agent_harness/run_service.py`

**Step 1: Write failing trust-boundary tests**

Reject mismatched repository, workflow, commit SHA, job identity, conclusion, schema, signature/digest, or artifact path. Verify that success imports contain summaries only and failure artifacts contain bounded sanitized diagnostics, never raw logs.

**Step 2: Confirm RED**

```bash
scripts/agent-harness --python -m unittest tests.test_github_evidence tests.test_governance
```

**Step 3: Implement import and workflow output**

Produce the same `GateSummary` schema in GitHub job summaries and a sanitized JSON artifact. Validate repository/workflow/commit/job/conclusion before local import. Add concurrency groups that cancel obsolete non-release PR runs; release and production runs must never be cancelled automatically.

**Step 4: Verify workflow structure and unit tests**

```bash
scripts/agent-harness --python -m unittest tests.test_github_evidence tests.test_governance tests.test_run_service
```

**Step 5: Commit**

```bash
git add tools/agent-harness/agent_harness/github_evidence.py tools/agent-harness/agent_harness/run_service.py tools/agent-harness/tests/test_github_evidence.py tools/agent-harness/tests/test_governance.py .github/workflows/platform-quality.yml .github/workflows/agent-governance.yml
git commit -m "feat(ci): exchange trusted gate summaries"
```

## 11. Task 9: Global and Project Policy Installation

**Files:**

- Create: `tools/agent-harness/agent_harness/global_policy.py`
- Create: `tools/agent-harness/tests/test_global_policy.py`
- Create: `tools/agent-harness/templates/global-token-rules.md`
- Create: `tools/agent-harness/templates/engineering-token-efficiency.md`
- Create: `docs/agent/TOKEN_EFFICIENCY.md`
- Modify: `tools/agent-harness/agent_harness/cli.py`
- Modify: `tools/agent-harness/tests/test_cli.py`
- Modify: `tools/agent-harness/README.md`
- Modify: `AGENTS.md`
- Modify: `docs/agent/AUTONOMY.md`
- Modify: `docs/agent/PROJECT_MEMORY.md`

**Step 1: Write failing marker and preservation tests**

Test dry-run, first install, repeated install, update, missing Codex home, malformed marker pairs, symlink escape, and preservation of all human-authored content outside managed blocks.

**Step 2: Confirm RED**

```bash
scripts/agent-harness --python -m unittest tests.test_global_policy
```

**Step 3: Implement reusable installation**

Add `install-global-policy --codex-home <path> [--dry-run]`. It may manage only marker-bounded sections in global `AGENTS.md`, `memories/engineering-token-efficiency.md`, and `memories/project-context-index.md`. Project rules point Agents to generated capsules and define diagnostic escalation; they must not require full Obsidian vault reads.

**Step 4: Verify and preview the real installation**

```bash
scripts/agent-harness --python -m unittest tests.test_global_policy tests.test_cli tests.test_governance
scripts/agent-harness install-global-policy --codex-home "$HOME/.codex" --dry-run
```

Inspect the dry-run diff. Apply to the real Codex home only after the diff contains no unrelated edits, then verify a second dry run is empty.

**Step 5: Commit repository files only**

```bash
git add tools/agent-harness/agent_harness/global_policy.py tools/agent-harness/agent_harness/cli.py tools/agent-harness/tests/test_global_policy.py tools/agent-harness/tests/test_cli.py tools/agent-harness/templates/global-token-rules.md tools/agent-harness/templates/engineering-token-efficiency.md tools/agent-harness/README.md docs/agent/TOKEN_EFFICIENCY.md docs/agent/AUTONOMY.md docs/agent/PROJECT_MEMORY.md AGENTS.md
git commit -m "feat(harness): install reusable token policy"
```

## 12. Task 10: Integration, Fault Drills, Benchmark, and Delivery Sync

**Files:**

- Create: `tools/agent-harness/tests/test_token_efficiency_integration.py`
- Create: `tools/agent-harness/tests/fixtures/token-baseline.json`
- Create: `docs/agent/run-history/2026-10-12-token-efficiency-verification.md`
- Modify: `tools/agent-harness/tests/test_fault_drills.py`
- Modify: `tools/agent-harness/README.md`
- Modify: `docs/agent/PROGRESS.md`
- Modify: `docs/superpowers/plans/2026-10-12-token-efficient-engineering.md`
- Modify after verification: configured Obsidian overview/progress machine blocks

**Step 1: Build the end-to-end fixture**

Exercise ten concurrent simulated Agents, one passing Gate, one failing Gate, one cache hit, one stale lease, one untrusted GitHub result, one vault outage, and one projection replay. Capture process count, capsule bytes, summary bytes, raw-context estimate, and active-startup estimate.

**Step 2: Run fault drills and full Harness tests**

```bash
scripts/agent-harness --python -m unittest discover -s tools/agent-harness/tests -p 'test_*.py' -v
```

No task is complete if this command fails. Keep raw output local; record only the bounded result in the verification report.

**Step 3: Run governance and representative project gates**

```bash
scripts/agent-harness doctor
scripts/agent-harness status --run-id <run-id>
scripts/agent-harness token-report --since 2026-10-09T14:22:00+08:00 --until 2026-10-12T23:59:59+08:00 --format json
```

Run the Gate Matrix profile selected by the Harness for the changed paths. Do not manually substitute a smaller profile.

**Step 4: Validate quantitative acceptance**

The integration report must state PASS/FAIL for every metric in section 2.3. A benchmark miss is not waived by prose; diagnose it, fix it, and rerun. Token forecasts remain estimates and must be labelled as such.

**Step 5: Independent review**

Use one fresh review Agent with only the final diff, design, this plan, and bounded verification evidence. Review for data leakage, unsafe cache reuse, concurrency races, schema downgrade, path traversal, and test bypass. Apply validated findings and rerun affected tests.

**Step 6: Sync delivery state**

Update `docs/agent/PROGRESS.md`, the plan status, and the existing Obsidian project overview/progress machine blocks with date, branch, commits, tests, measured benchmark, residual risks, and the next Wave 2 checkpoint. Do not create duplicate top-level status documents.

**Step 7: Commit and push**

Before pushing, read `~/.codex/memories/claude-code-migration/github-repositories.md`, verify the remote and branch again, and ensure unrelated Wave 2 changes are not staged.

```bash
git add tools/agent-harness/tests/test_token_efficiency_integration.py tools/agent-harness/tests/fixtures/token-baseline.json tools/agent-harness/tests/test_fault_drills.py tools/agent-harness/README.md docs/agent/run-history/2026-10-12-token-efficiency-verification.md docs/agent/PROGRESS.md docs/superpowers/plans/2026-10-12-token-efficient-engineering.md
git commit -m "test(harness): verify token efficiency control plane"
git push origin feat/admin-product-alignment
```

If the push is rejected, the branch is protected, tests fail, or unrelated changes cannot be safely separated, stop and report the exact condition. Do not merge, rebase, force-push, or clean the worktree automatically.

## 13. Return to Product Delivery

After Task 10 passes:

1. Restore `docs/superpowers/plans/2026-10-11-template-branding-wave2.md` as the active product plan in Harness state.
2. Resume at Task 2's real acceptance boundary: PHPUnit first, then branded image publish/preview validation against both supported databases.
3. Generate one Context Capsule for that checkpoint instead of reloading the full historical plan, report, and Obsidian vault.
4. Continue to Wave 2 Task 3 only after the existing Task 2 acceptance assertions pass.
5. Measure actual Token use after the first two product milestones and compare it with section 2.2; adjust budgets from evidence, not aspiration.

## 14. Definition of Done

- All ten tasks are committed in scoped commits with no unrelated Wave 2 file included.
- Full Harness tests and selected project gates pass.
- Passing gates expose summaries only; failure diagnostics meet the 4 KiB limit.
- Evidence reuse is demonstrably safe and strict HEAD gates cannot be downgraded.
- Concurrent duplicate gates execute once.
- Context Capsules meet all byte budgets and provenance checks.
- Token telemetry is accurate for fixtures and contains no content payloads.
- Obsidian synchronization is one-way, marker-bounded, conflict-aware, and recoverable.
- GitHub evidence import validates repository, workflow, commit, job, and conclusion.
- Global policy installation is idempotent and preserves unmanaged content.
- Benchmark and verification report record measured savings and any remaining gap.
- Repository docs, GitHub branch, and Obsidian projection are synchronized before Wave 2 resumes.
